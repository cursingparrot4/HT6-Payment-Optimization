# SwitchPay Freesolo Runbook

This environment trains a single-turn SFT model for:

```text
payment goal text -> strict SwitchPay Intent JSON
```

The API key must stay in the shell environment. Never write it into this folder.

## Prepare Data

```bash
.venv/bin/python -m intent.gen_data --out intent/training/freesolo/dataset --train-rows 240
```

## Publish Environment

```bash
export FREESOLO_API_KEY=...
export SSL_CERT_FILE=.freesolo-venv/lib/python3.11/site-packages/certifi/cacert.pem
.freesolo-venv/bin/flash env push --name switchpay-intent intent/training/freesolo
```

Paste the returned environment id into `configs/sft.toml`.

## Validate And Train

```bash
.freesolo-venv/bin/flash train intent/training/freesolo/configs/sft.toml --dry-run
.freesolo-venv/bin/flash train intent/training/freesolo/configs/sft.toml --cost
.freesolo-venv/bin/flash train intent/training/freesolo/configs/sft.toml --background
```

After the run finishes:

```bash
.freesolo-venv/bin/flash deploy <run-id>
.freesolo-venv/bin/flash deployments --json
```

Use the deployment's `openai_base_url` and run id to start FastAPI with:

```bash
FREESOLO_API_KEY=...
FREESOLO_BASE_URL=...
FREESOLO_MODEL=<run-id>
```
