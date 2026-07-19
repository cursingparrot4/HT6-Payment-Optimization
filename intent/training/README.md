# Freesolo Training Artifacts

This directory records confirmed Freesolo upload/inference instructions and reproducibility metadata after platform access is available. Do not guess the vendor schema from the internal JSONL format.

A real run record must include base model/revision, SFT hyperparameters, dataset SHA-256 and row counts, run ID/status, output model ID, and endpoint contract. Never commit API keys. SFT is the only permitted training method; reinforcement learning and a second trained model are out of scope.

## Local preparation complete

The repository now has a provider-independent internal record format, seeded latent sampling, split-before-paraphrase behavior, cache/resume, global dedupe, atomic JSONL output, and manifests containing prompt/NumPy versions plus train/test/combined hashes. Fixture-generated rows are marked `synthetic_fixture=true` and cannot support production claims.

## Required before a concrete adapter

Record all of the following from current Freesolo documentation before implementing transport code:

1. Documentation URL/version and verification date.
2. Authentication header and endpoint base URL.
3. Accepted SFT JSONL shape and whether metadata fields are allowed.
4. Supported small base model ID/revision.
5. Training hyperparameter fields and defaults.
6. Training status/run/model identifiers.
7. Inference request, structured-output option, response shape, and timeout guidance.

Do not place internal `metadata` into an upload unless the confirmed platform schema permits it. Do not write a generic guessed `messages` uploader and call it Freesolo-compatible.
