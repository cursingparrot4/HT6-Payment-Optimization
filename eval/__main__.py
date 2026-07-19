"""Run the frozen intent evaluation from the command line.

Usage:
    python -m eval [--resamples N] [--seed N] [--refresh] [--no-monthly]

Runners are constructed from environment credentials; roles without configured
credentials are honestly omitted, which forces a "partial" report. Fallback is
structurally disabled: the harness parses raw model text directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from eval.dataset import load_frozen_dataset, load_manifest_sha256
from eval.harness import run_evaluation_sync
from eval.models import ModelRole
from eval.report import render_markdown, write_report
from eval.runners import ModelRunner, ProviderModelRunner
from intent.providers import FreesoloIntentProvider, GeminiIntentProvider

DATASET_PATH = Path("intent/training/freesolo/dataset/eval.jsonl")
MANIFEST_PATH = Path("intent/training/freesolo/dataset_manifest.json")
CACHE_DIR = Path("eval/cache")
REPORTS_DIR = Path("eval/reports")


def build_runners() -> list[ModelRunner]:
    runners: list[ModelRunner] = []
    if os.getenv("FREESOLO_API_KEY") and os.getenv("FREESOLO_BASE_URL"):
        runners.append(
            ProviderModelRunner(
                provider=FreesoloIntentProvider(),
                model_role=ModelRole.TRAINED_SLM,
                runner_id="freesolo-trained",
            )
        )
        base_model = os.getenv("FREESOLO_BASE_MODEL")
        if base_model:
            runners.append(
                ProviderModelRunner(
                    provider=FreesoloIntentProvider(model_id=base_model),
                    model_role=ModelRole.BASE_SLM,
                    runner_id="freesolo-base",
                )
            )
    if os.getenv("GEMINI_API_KEY"):
        runners.append(
            ProviderModelRunner(
                provider=GeminiIntentProvider(),
                model_role=ModelRole.BIG_PROMPTED,
                runner_id="gemini-prompted",
            )
        )
    return runners


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m eval")
    parser.add_argument("--resamples", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-monthly", action="store_true")
    args = parser.parse_args()

    load_dotenv(".env")
    runners = build_runners()
    if not runners:
        print("No model credentials configured; nothing to evaluate.", file=sys.stderr)
        return 2

    dataset = load_frozen_dataset(
        DATASET_PATH, expected_sha256=load_manifest_sha256(MANIFEST_PATH)
    )
    print(
        f"Frozen dataset: {len(dataset.examples)} examples, sha256 {dataset.dataset_sha256[:16]}…"
    )
    print("Runners: " + ", ".join(f"{r.runner_id} ({r.model_role.value})" for r in runners))

    report = run_evaluation_sync(
        dataset,
        runners,
        cache_dir=CACHE_DIR,
        seed=args.seed,
        resamples=args.resamples,
        refresh=args.refresh,
        include_monthly=not args.no_monthly,
    )
    run_dir = write_report(report, REPORTS_DIR)
    print(f"\nReport written to {run_dir}\n")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
