"""Provider-independent reverse generation of deterministic SFT JSONL records."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from intent.manifests import (
    canonical_json,
    load_provider_cache,
    provider_cache_key,
    sha256_bytes,
    write_jsonl,
    write_manifest,
    write_provider_cache,
)
from intent.models import (
    DatasetSplit,
    GeneratedDataset,
    GenerationManifest,
    IntentCardContext,
    LanguageStyle,
    LatentIntent,
    ProviderResponse,
    SftMessage,
    SftMetadata,
    SftRecord,
)
from intent.prompts import INTENT_PROMPT_VERSION, build_intent_system_prompt
from intent.providers import ParaphraseProvider
from intent.sampling import (
    SamplingConfig,
    latent_target_json,
    sample_latent_intents,
    split_latents,
)


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    sampling: SamplingConfig
    output_dir: Path
    cache_dir: Path
    paraphrases_per_latent: int = 2
    production_claim_allowed: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.paraphrases_per_latent, bool)
            or not isinstance(self.paraphrases_per_latent, int)
            or not 1 <= self.paraphrases_per_latent <= 3
        ):
            raise ValueError("paraphrases_per_latent must be an integer from 1 through 3")


def normalize_description(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    normalized = " ".join(normalized.split())
    return normalized.strip(" \t\r\n.,!?;:'\"")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_paraphrases(response: ProviderResponse, requested: int) -> list[str]:
    try:
        parsed = json.loads(
            response.raw_text,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"paraphrase response is invalid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("paraphrase response must be a nonempty JSON list")
    phrases = []
    for value in parsed[:requested]:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("every paraphrase must be a nonempty string")
        if len(value) > 2_000:
            raise ValueError("paraphrase exceeds 2,000 characters")
        phrases.append(value.strip())
    return phrases


async def _response_for_latent(
    latent: LatentIntent,
    provider: ParaphraseProvider,
    config: GenerationConfig,
) -> ProviderResponse:
    target = latent_target_json(latent)
    latent_request = canonical_json(latent.model_dump(mode="json"))
    key = provider_cache_key(
        provider.name,
        provider.model_id,
        INTENT_PROMPT_VERSION,
        latent_request + "\n" + target,
        config.paraphrases_per_latent,
    )
    cache_path = config.cache_dir / f"{key}.json"
    cached = load_provider_cache(cache_path)
    if (
        cached is not None
        and cached.provider_name == provider.name
        and cached.model_id == provider.model_id
    ):
        return cached
    response = await provider.generate_paraphrases(
        latent,
        config.paraphrases_per_latent,
    )
    if response.provider_name != provider.name or response.model_id != provider.model_id:
        raise ValueError("paraphrase provider response identity mismatch")
    write_provider_cache(cache_path, response)
    return response


def _record(
    latent: LatentIntent,
    split: DatasetSplit,
    description: str,
    style: LanguageStyle,
    response: ProviderResponse,
    *,
    synthetic_fixture: bool,
) -> SftRecord:
    return SftRecord(
        messages=[
            SftMessage(
                role="system",
                content=build_intent_system_prompt(
                    latent.reference_date,
                    latent.card_context,
                ),
            ),
            SftMessage(role="user", content=description),
            SftMessage(role="assistant", content=latent_target_json(latent)),
        ],
        metadata=SftMetadata(
            latent_id=latent.latent_id,
            split=split,
            prompt_version=INTENT_PROMPT_VERSION,
            regime=latent.regime,
            style=style,
            provider_name=response.provider_name,
            model_id=response.model_id,
            synthetic_fixture=synthetic_fixture,
        ),
    )


async def generate_sft_dataset(
    provider: ParaphraseProvider,
    config: GenerationConfig,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
) -> GeneratedDataset:
    latents = sample_latent_intents(config.sampling, reference_date, card_context)
    train_latents, test_latents = split_latents(
        latents,
        seed=config.sampling.seed,
        test_fraction_bps=config.sampling.test_fraction_bps,
    )
    synthetic_fixture = bool(getattr(provider, "synthetic_fixture", False))
    seen_descriptions: set[str] = set()
    train_records: list[SftRecord] = []
    test_records: list[SftRecord] = []
    duplicate_count = 0
    rejected_count = 0

    for split, split_latents_list, output in (
        (DatasetSplit.TRAIN, train_latents, train_records),
        (DatasetSplit.TEST, test_latents, test_records),
    ):
        for latent in split_latents_list:
            try:
                response = await _response_for_latent(latent, provider, config)
                phrases = _parse_paraphrases(
                    response,
                    config.paraphrases_per_latent,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                rejected_count += 1
                continue
            except Exception:
                rejected_count += 1
                continue
            for index, phrase in enumerate(phrases):
                normalized = normalize_description(phrase)
                if not normalized or normalized in seen_descriptions:
                    duplicate_count += 1
                    continue
                seen_descriptions.add(normalized)
                style = latent.styles[index % len(latent.styles)]
                output.append(
                    _record(
                        latent,
                        split,
                        phrase,
                        style,
                        response,
                        synthetic_fixture=synthetic_fixture,
                    )
                )

    train_path = config.output_dir / "train.jsonl"
    test_path = config.output_dir / "test.jsonl"
    train_hash = write_jsonl(train_path, train_records)
    test_hash = write_jsonl(test_path, test_records)
    manifest = GenerationManifest(
        prompt_version=INTENT_PROMPT_VERSION,
        numpy_version=np.__version__,
        seed=config.sampling.seed,
        test_fraction_bps=config.sampling.test_fraction_bps,
        paraphrases_per_latent=config.paraphrases_per_latent,
        latent_count=len(latents),
        train_latent_count=len(train_latents),
        test_latent_count=len(test_latents),
        train_record_count=len(train_records),
        test_record_count=len(test_records),
        accepted_record_count=len(train_records) + len(test_records),
        rejected_response_count=rejected_count,
        retry_count=0,
        duplicate_description_count=duplicate_count,
        provider_name=provider.name,
        model_id=provider.model_id,
        synthetic_fixture=synthetic_fixture,
        production_claim_allowed=(
            config.production_claim_allowed and not synthetic_fixture
        ),
        train_sha256=train_hash,
        test_sha256=test_hash,
        dataset_sha256=sha256_bytes(f"{train_hash}:{test_hash}".encode("ascii")),
    )
    write_manifest(config.output_dir / "manifest.json", manifest)
    return GeneratedDataset(
        train_records=train_records,
        test_records=test_records,
        manifest=manifest,
    )
