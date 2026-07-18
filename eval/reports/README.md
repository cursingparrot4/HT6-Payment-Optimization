# Evaluation Reports

Final measured reports are generated from frozen cached outputs by `eval/report.py`. Commit the Markdown narrative and provenance needed for judging; keep large/raw JSON outputs gitignored or attach them as release artifacts according to team policy.

A final report is valid only when trained SLM, matching base SLM, and big prompted model all ran against the same dataset/prompt/probe hashes with parser fallback disabled. Fixture-runner reports must be labeled synthetic and cannot overwrite the final report.
