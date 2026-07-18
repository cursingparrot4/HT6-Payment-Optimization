# External Tests

Credentialed Freesolo/general-model smoke tests live here. They require the `external` pytest marker plus explicit `RUN_EXTERNAL_TESTS=true`, never run by default, never use runtime fallback, and must not log or serialize secrets.
