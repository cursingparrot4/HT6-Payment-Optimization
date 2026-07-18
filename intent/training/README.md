# Freesolo Training Artifacts

This directory records confirmed Freesolo upload/inference instructions and reproducibility metadata after platform access is available. Do not guess the vendor schema from the internal JSONL format.

A real run record must include base model/revision, SFT hyperparameters, dataset SHA-256 and row counts, run ID/status, output model ID, and endpoint contract. Never commit API keys. SFT is the only permitted training method; reinforcement learning and a second trained model are out of scope.
