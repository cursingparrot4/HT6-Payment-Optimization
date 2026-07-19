# Freesolo Training Artifacts

This directory records confirmed Freesolo upload/inference instructions and reproducibility metadata after platform access is available. Do not guess the vendor schema from the internal JSONL format.

A real run record must include base model/revision, SFT hyperparameters, dataset SHA-256 and row counts, run ID/status, output model ID, and endpoint contract. Never commit API keys. SFT is the only permitted training method; reinforcement learning and a second trained model are out of scope.

## Runtime Freesolo Adapter

The app can call a trained Freesolo intent model through the Goal parser when these environment variables are set on the FastAPI process:

- `FREESOLO_API_KEY`
- `FREESOLO_BASE_URL`
- `FREESOLO_MODEL`
- `FREESOLO_CHAT_COMPLETIONS_PATH` optional, default `/v1/chat/completions`

The adapter assumes an OpenAI-compatible chat-completions response unless the confirmed Freesolo endpoint contract differs. If it differs, update `intent/providers.py` and record the confirmed contract in the run manifest.
