"""Frozen held-out dataset loading and hashing.

The frozen set is the committed Freesolo task-record file
``intent/training/freesolo/dataset/eval.jsonl`` — the exact rows excluded from the
SFT run, hash-pinned by ``dataset_manifest.json``. Each record embeds the card
context, reference date, and user text inside one prompt string with fixed markers;
this loader extracts those fields deterministically and validates every gold target
through the same strict parser the runtime uses. A gold row that fails strict
validation is a dataset error, surfaced at load rather than absorbed into metrics.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from eval.models import EvalExample, ExampleSource, FrozenDataset
from intent.manifests import sha256_bytes
from intent.models import IntentCardContext
from intent.parser import IntentParseError, parse_provider_output

_REFERENCE_DATE_RE = re.compile(r"Reference date is (\d{4}-\d{2}-\d{2})\.")
_CARD_CONTEXT_MARKER = "Synthetic card context:\n"
_USER_TEXT_MARKER = "User text:\n"
_PROMPT_VERSION = "intent-v1"


class DatasetError(ValueError):
    """A frozen dataset file is malformed or fails its integrity checks."""


def _extract_example(index: int, record: dict[str, object], path: Path) -> EvalExample:
    if set(record) != {"input", "output"}:
        raise DatasetError(f"{path.name}:{index + 1}: expected exactly input/output fields")
    prompt = record["input"]
    gold_raw = record["output"]
    if not isinstance(prompt, str) or not isinstance(gold_raw, str):
        raise DatasetError(f"{path.name}:{index + 1}: input and output must be strings")

    date_match = _REFERENCE_DATE_RE.search(prompt)
    if date_match is None:
        raise DatasetError(f"{path.name}:{index + 1}: prompt lacks a reference date")
    reference_date = date.fromisoformat(date_match.group(1))

    context_start = prompt.find(_CARD_CONTEXT_MARKER)
    user_start = prompt.find(_USER_TEXT_MARKER)
    if context_start < 0 or user_start < 0 or user_start <= context_start:
        raise DatasetError(f"{path.name}:{index + 1}: prompt lacks card-context/user markers")
    context_json = prompt[context_start + len(_CARD_CONTEXT_MARKER) : user_start].strip()
    user_text = prompt[user_start + len(_USER_TEXT_MARKER) :].strip()
    if not user_text:
        raise DatasetError(f"{path.name}:{index + 1}: empty user text")

    try:
        raw_context = json.loads(context_json)
        card_context = tuple(IntentCardContext.model_validate(card) for card in raw_context)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DatasetError(f"{path.name}:{index + 1}: invalid card context: {exc}") from exc

    try:
        gold = parse_provider_output(gold_raw, card_context)
    except IntentParseError as exc:
        raise DatasetError(
            f"{path.name}:{index + 1}: gold output fails strict parse: {exc}"
        ) from exc

    return EvalExample(
        # Zero-padded ids keep lexicographic order equal to file order (§3 stable sort).
        example_id=f"eval-{index:04d}",
        user_text=user_text,
        gold_raw=gold_raw,
        gold_intent=gold.intent,
        reference_date=reference_date,
        card_context=card_context,
        source=ExampleSource.GENERATED_TEST,
    )


def load_frozen_dataset(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> FrozenDataset:
    """Load, extract, and integrity-check the complete ordered held-out set."""

    if not path.is_file():
        raise DatasetError(f"frozen dataset not found: {path}")
    # Normalize line endings before hashing: git may check the committed LF file out
    # as CRLF on Windows, and the manifest hash was computed over the LF bytes.
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    dataset_sha256 = sha256_bytes(normalized)
    if expected_sha256 is not None and dataset_sha256 != expected_sha256:
        raise DatasetError(
            f"frozen dataset hash mismatch: manifest {expected_sha256} != file {dataset_sha256}"
        )

    examples: list[EvalExample] = []
    seen_texts: set[str] = set()
    duplicate_text_count = 0
    for index, line in enumerate(normalized.decode("utf-8").splitlines()):
        if not line.strip():
            raise DatasetError(f"{path.name}:{index + 1}: blank line in frozen dataset")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name}:{index + 1}: invalid JSON: {exc}") from exc
        example = _extract_example(index, record, path)
        # The committed frozen set contains repeated phrasings; the artifact is
        # hash-pinned, so duplication is reported as provenance, never "repaired".
        normalized_text = " ".join(example.user_text.lower().split())
        if normalized_text in seen_texts:
            duplicate_text_count += 1
        seen_texts.add(normalized_text)
        examples.append(example)

    if not examples:
        raise DatasetError(f"{path} contains no examples")
    return FrozenDataset(
        path=str(path).replace("\\", "/"),
        dataset_sha256=dataset_sha256,
        prompt_version=_PROMPT_VERSION,
        duplicate_text_count=duplicate_text_count,
        examples=tuple(examples),
    )


def load_manifest_sha256(manifest_path: Path) -> str:
    """Read the pinned eval-set hash from the committed dataset manifest."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = manifest["eval_sha256"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise DatasetError(f"cannot read eval_sha256 from {manifest_path}: {exc}") from exc
    if not isinstance(value, str) or len(value) != 64:
        raise DatasetError(f"{manifest_path} eval_sha256 is not a sha256 hex digest")
    return value
