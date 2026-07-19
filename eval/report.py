"""Report rendering and safe persistence.

JSON is the source of truth; Markdown is rendered from the identical values so the
two can never disagree. Writes go to a fresh run directory and the ``latest.json``
pointer moves only after the report validates. Fixture reports are quarantined in a
``fixture/`` subdirectory and can never claim final status or overwrite a measured
run (IMPLEMENTATION.md §5/§14/§15).
"""

from __future__ import annotations

import os
from pathlib import Path

from eval.models import EvalReport, RunnerReport, SubsetMetrics
from intent.manifests import canonical_json


class ReportError(ValueError):
    """A report failed validation or attempted an unsafe write."""


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _mae(metrics: SubsetMetrics) -> str:
    if metrics.weight_mae_ppm is None:
        return "n/a"
    return f"{metrics.weight_mae_ppm:,} ppm (n={metrics.weight_mae_valid_count})"


def _downstream(metrics: SubsetMetrics) -> str:
    if metrics.downstream_match_rate is None:
        return "n/a"
    text = _rate(metrics.downstream_match_rate)
    interval = metrics.downstream_interval
    if interval is not None:
        text += f" [{interval.lower * 100:.1f}, {interval.upper * 100:.1f}]"
    return text


def _monthly(metrics: SubsetMetrics) -> str:
    if metrics.monthly_mean_agreement is None:
        return f"n/a (unavailable={metrics.monthly_unavailable_count})"
    return (
        f"{_rate(metrics.monthly_mean_agreement)} mean / "
        f"{_rate(metrics.monthly_exact_match_rate)} exact "
        f"(unavailable={metrics.monthly_unavailable_count})"
    )


def _runner_row(report: RunnerReport) -> str:
    m = report.overall
    constraints = m.constraints
    constraint_text = (
        f"{constraints.whole_constraint_exact}/{constraints.denominator} exact, "
        f"F1 {constraints.f1:.2f}"
        if constraints is not None
        else "n/a"
    )
    return (
        f"| {report.model_role.value} | {report.provider_name}/{report.model_id} | "
        f"{_rate(m.schema_valid_rate)} | {_mae(m)} | {constraint_text} | "
        f"{_downstream(m)} | {_monthly(m)} |"
    )


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# Intent Parser Evaluation Report",
        "",
        f"- Status: **{report.status}**",
        f"- Evaluated at: {report.evaluated_at_utc}",
        f"- Frozen dataset: `{report.dataset_path}` "
        f"({report.example_count} examples: {report.generated_count} generated, "
        f"{report.adversarial_count} adversarial)",
        f"- Dataset sha256: `{report.dataset_sha256}`",
        f"- Prompt contract: `{report.prompt_version}` / `{report.prompt_sha256[:16]}…`",
        f"- Engine config hash: `{report.engine_config_hash[:16]}…`",
        f"- Probe suite: `{report.probe_suite_sha256[:16]}…` · "
        f"monthly scenario: `{report.monthly_scenario_sha256[:16]}…`",
        f"- Bootstrap: seed {report.bootstrap_seed}, {report.bootstrap_resamples} resamples",
        "",
    ]
    if report.missing_roles:
        lines.append(
            "> **Not a comparative result.** Missing model roles: "
            + ", ".join(role.value for role in report.missing_roles)
            + "."
        )
        lines.append("")
    lines.extend(
        [
            "| Role | Model | Schema-valid | Weight MAE | Constraints | "
            "Downstream match [95% CI] | Monthly agreement |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_runner_row(runner) for runner in report.runners)
    lines.append("")
    for runner in report.runners:
        if runner.error_counts:
            errors = ", ".join(
                f"{category.value}: {count}"
                for category, count in sorted(runner.error_counts.items())
            )
            lines.append(f"- `{runner.runner_id}` errors — {errors}")
    for warning in report.warnings:
        lines.append(f"- ⚠ {warning}")
    lines.append("")
    lines.append(
        "Raw JSON parse rate and per-goal MAE are in `report.json`; the Markdown table "
        "shows schema-valid rate because that is what the engine can consume."
    )
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, reports_dir: Path) -> Path:
    """Persist a validated report into a fresh run directory; move `latest` last."""

    # Construction already ran model validation; re-dump to guarantee round-trip.
    EvalReport.model_validate(report.model_dump(mode="json"))

    target_root = reports_dir / "fixture" if report.status == "fixture" else reports_dir
    stamp = report.evaluated_at_utc.replace(":", "").replace("-", "")
    run_dir = target_root / f"run-{stamp}-{report.status}"
    if run_dir.exists():
        raise ReportError(f"refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(
        canonical_json(report.model_dump(mode="json")), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")

    if report.status != "fixture":
        pointer = reports_dir / "latest.json"
        tmp = pointer.with_suffix(".tmp")
        tmp.write_text(
            canonical_json({"run_dir": run_dir.name, "status": report.status}),
            encoding="utf-8",
        )
        os.replace(tmp, pointer)
    return run_dir
