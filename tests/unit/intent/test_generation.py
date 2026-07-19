from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from engine.objective import quantize_intent_weights
from intent.gen_data import GenerationConfig, generate_sft_dataset
from intent.manifests import sha256_file
from intent.models import DatasetSplit, IntentCardContext, SftRecord
from intent.parser import parse_provider_output
from intent.providers import FixtureParaphraseProvider
from intent.sampling import SamplingConfig


def contexts() -> tuple[IntentCardContext, ...]:
    return (
        IntentCardContext(id="bonus-card", name="Bonus Card", has_active_bonus=True),
        IntentCardContext(id="plain-card", name="Plain Card", has_active_bonus=False),
    )


def fixture_responses(latent_count: int, *, duplicate: bool = False) -> dict[str, list[str]]:
    return {
        f"latent-{index:06d}": (
            ["same description", " Same   Description! "]
            if duplicate
            else [
                f"Latent {index} concise payment goal",
                f"Please use payment preference number {index}",
            ]
        )
        for index in range(latent_count)
    }


class MalformedFixtureProvider:
    name = "fixture"
    model_id = "fixture-paraphrase-v1"
    synthetic_fixture = True

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self.delegate = FixtureParaphraseProvider(responses)

    async def generate_paraphrases(self, latent, count):
        response = await self.delegate.generate_paraphrases(latent, count)
        if latent.latent_id == "latent-000001":
            return response.model_copy(update={"raw_text": "not-json"})
        return response


class TimeoutFixtureProvider(MalformedFixtureProvider):
    async def generate_paraphrases(self, latent, count):
        if latent.latent_id == "latent-000001":
            raise TimeoutError("synthetic timeout")
        return await self.delegate.generate_paraphrases(latent, count)


class CancelledFixtureProvider(MalformedFixtureProvider):
    async def generate_paraphrases(self, latent, count):
        raise asyncio.CancelledError


def generate(
    tmp_path: Path,
    provider: FixtureParaphraseProvider,
    *,
    latent_count: int = 10,
    paraphrases: int = 2,
    cache_name: str = "cache",
):
    config = GenerationConfig(
        sampling=SamplingConfig(
            seed=42,
            latent_count=latent_count,
            test_fraction_bps=2_000,
        ),
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / cache_name,
        paraphrases_per_latent=paraphrases,
        production_claim_allowed=True,
    )
    return asyncio.run(
        generate_sft_dataset(
            provider,
            config,
            date(2026, 7, 18),
            contexts(),
        )
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_generation_writes_valid_leak_free_hashed_jsonl(tmp_path: Path) -> None:
    provider = FixtureParaphraseProvider(fixture_responses(10))
    generated = generate(tmp_path, provider)
    train_path = tmp_path / "output" / "train.jsonl"
    test_path = tmp_path / "output" / "test.jsonl"
    manifest_path = tmp_path / "output" / "manifest.json"

    assert provider.call_count == 10
    assert len(generated.train_records) == 16
    assert len(generated.test_records) == 4
    assert generated.manifest.synthetic_fixture is True
    assert generated.manifest.production_claim_allowed is False
    assert generated.manifest.numpy_version
    assert generated.manifest.train_sha256 == sha256_file(train_path)
    assert generated.manifest.test_sha256 == sha256_file(test_path)
    expected_dataset_hash = hashlib.sha256(
        (
            generated.manifest.train_sha256
            + ":"
            + generated.manifest.test_sha256
        ).encode("ascii")
    ).hexdigest()
    assert generated.manifest.dataset_sha256 == expected_dataset_hash
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["schema_version"] == "1.0"
    assert len(read_jsonl(train_path)) == 16
    assert len(read_jsonl(test_path)) == 4

    train_latents = {record.metadata.latent_id for record in generated.train_records}
    test_latents = {record.metadata.latent_id for record in generated.test_records}
    assert train_latents.isdisjoint(test_latents)
    for record in [*generated.train_records, *generated.test_records]:
        reparsed = SftRecord.model_validate(record.model_dump())
        target = parse_provider_output(reparsed.messages[2].content, contexts()).intent
        assert sum(quantize_intent_weights(target).values()) == 1_000_000
        assert reparsed.metadata.synthetic_fixture is True


def test_generation_resume_uses_cache_without_provider_calls(tmp_path: Path) -> None:
    first_provider = FixtureParaphraseProvider(fixture_responses(6))
    first = generate(tmp_path, first_provider, latent_count=6)
    assert first_provider.call_count == 6

    second_provider = FixtureParaphraseProvider({})
    second = generate(tmp_path, second_provider, latent_count=6)

    assert second_provider.call_count == 0
    assert first.manifest.model_dump() == second.manifest.model_dump()
    assert [record.model_dump() for record in first.train_records] == [
        record.model_dump() for record in second.train_records
    ]


def test_global_normalized_dedupe_includes_cross_split_duplicates(tmp_path: Path) -> None:
    provider = FixtureParaphraseProvider(fixture_responses(10, duplicate=True))
    generated = generate(tmp_path, provider)

    assert generated.manifest.accepted_record_count == 1
    assert generated.manifest.duplicate_description_count == 19
    assert len(generated.train_records) == 1
    assert generated.test_records == []


def test_malformed_provider_response_is_rejected_and_counted(tmp_path: Path) -> None:
    provider = MalformedFixtureProvider(fixture_responses(4, duplicate=False))
    generated = generate(tmp_path, provider, latent_count=4)

    assert generated.manifest.rejected_response_count == 1
    assert generated.manifest.accepted_record_count == 6


def test_split_metadata_matches_output_file(tmp_path: Path) -> None:
    generated = generate(
        tmp_path,
        FixtureParaphraseProvider(fixture_responses(5)),
        latent_count=5,
        paraphrases=1,
    )
    assert all(
        record.metadata.split is DatasetSplit.TRAIN
        for record in generated.train_records
    )
    assert all(
        record.metadata.split is DatasetSplit.TEST
        for record in generated.test_records
    )


def test_all_rejected_responses_write_valid_empty_artifacts(tmp_path: Path) -> None:
    generated = generate(
        tmp_path,
        FixtureParaphraseProvider({}),
        latent_count=4,
        paraphrases=1,
    )

    assert generated.train_records == []
    assert generated.test_records == []
    assert generated.manifest.accepted_record_count == 0
    assert generated.manifest.rejected_response_count == 4
    assert (tmp_path / "output" / "train.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "output" / "test.jsonl").read_text(encoding="utf-8") == ""


def test_generation_counts_native_timeout_but_propagates_cancellation(tmp_path: Path) -> None:
    timed = generate(
        tmp_path,
        TimeoutFixtureProvider(fixture_responses(4)),
        latent_count=4,
        paraphrases=1,
    )
    assert timed.manifest.rejected_response_count == 1
    assert timed.manifest.accepted_record_count == 3

    with pytest.raises(asyncio.CancelledError):
        generate(
            tmp_path / "cancelled",
            CancelledFixtureProvider(fixture_responses(4)),
            latent_count=4,
            paraphrases=1,
        )


def test_cache_key_changes_when_safe_card_context_changes(tmp_path: Path) -> None:
    first_provider = FixtureParaphraseProvider(fixture_responses(4))
    config = GenerationConfig(
        sampling=SamplingConfig(seed=42, latent_count=4, test_fraction_bps=2_000),
        output_dir=tmp_path / "first",
        cache_dir=tmp_path / "cache",
        paraphrases_per_latent=1,
    )
    asyncio.run(
        generate_sft_dataset(
            first_provider,
            config,
            date(2026, 7, 18),
            contexts(),
        )
    )
    changed_context = tuple(
        card.model_copy(update={"name": card.name + " Updated"}) for card in contexts()
    )
    second_provider = FixtureParaphraseProvider(fixture_responses(4))
    asyncio.run(
        generate_sft_dataset(
            second_provider,
            config,
            date(2026, 7, 18),
            changed_context,
        )
    )

    assert first_provider.call_count == 4
    assert second_provider.call_count == 4


def test_paraphrase_json_rejects_nonstandard_constants(tmp_path: Path) -> None:
    class NanProvider(MalformedFixtureProvider):
        async def generate_paraphrases(self, latent, count):
            response = await self.delegate.generate_paraphrases(latent, count)
            return response.model_copy(update={"raw_text": "[NaN]"})

    generated = generate(
        tmp_path,
        NanProvider(fixture_responses(3)),
        latent_count=3,
        paraphrases=1,
    )
    assert generated.manifest.accepted_record_count == 0
    assert generated.manifest.rejected_response_count == 3


def test_corrupted_cache_is_regenerated_instead_of_rejecting_row(tmp_path: Path) -> None:
    first_provider = FixtureParaphraseProvider(fixture_responses(4))
    generate(tmp_path, first_provider, latent_count=4, paraphrases=1)
    cache_files = sorted((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 4
    cache_files[0].write_text("not-json", encoding="utf-8")

    second_provider = FixtureParaphraseProvider(fixture_responses(4))
    regenerated = generate(tmp_path, second_provider, latent_count=4, paraphrases=1)

    assert second_provider.call_count == 1
    assert regenerated.manifest.accepted_record_count == 4
    assert regenerated.manifest.rejected_response_count == 0
