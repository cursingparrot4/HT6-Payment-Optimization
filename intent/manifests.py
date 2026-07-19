"""Canonical hashes and atomic persistence for intent-data artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from intent.models import GenerationManifest, ProviderResponse, SftRecord


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_jsonl(path: Path, records: list[SftRecord]) -> str:
    content = "".join(
        canonical_json(record.model_dump(mode="json")) + "\n" for record in records
    ).encode("utf-8")
    _atomic_write(path, content)
    return sha256_bytes(content)


def write_manifest(path: Path, manifest: GenerationManifest) -> None:
    content = (canonical_json(manifest.model_dump(mode="json")) + "\n").encode("utf-8")
    _atomic_write(path, content)


def provider_cache_key(
    provider_name: str,
    model_id: str,
    prompt_version: str,
    latent_json: str,
    count: int,
) -> str:
    payload = canonical_json(
        {
            "count": count,
            "latent": latent_json,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "provider_name": provider_name,
        }
    )
    return sha256_bytes(payload.encode("utf-8"))


def load_provider_cache(path: Path) -> ProviderResponse | None:
    if not path.exists():
        return None
    try:
        return ProviderResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        return None


def write_provider_cache(path: Path, response: ProviderResponse) -> None:
    safe_response = response.model_copy(update={"cached": True})
    content = (canonical_json(safe_response.model_dump(mode="json")) + "\n").encode(
        "utf-8"
    )
    _atomic_write(path, content)
