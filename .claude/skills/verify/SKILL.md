---
name: verify
description: Exercise the bmlsub subtitle CLI and Python API through public surfaces.
---

# Verify bmlsub

Use the Conda base environment and a temporary workspace. Install the package editable so the real
`bmlsub` console entry point is used. Do not use the unittest suite as runtime verification evidence.

Run a deterministic local HTTP stub that accepts Fanhuaji-style form data and returns framed
conversion-unit text with a few Simplified characters replaced. Then drive the installed CLI:

1. create a valid CHS ASS in a path containing spaces and Chinese characters;
2. run `bmlsub episode validate --ensure-cht` against the local provider and capture stdout JSON,
   stderr diagnostics, exit code, and generated CHT;
3. run the same command again and confirm `skipped` with `reused=true` without a second provider call;
4. modify the CHS file and run again, confirming a new successful execution;
5. from a fresh process, run `bmlsub run show <run-id>` and confirm persisted run/stage/artifact data;
6. create ambiguous ASS input, preserve an existing CHT, and confirm exit code 2, `needs_review`, and
   unchanged CHT;
7. run public `Pipeline.validate_subtitles()` with an injected provider and confirm it returns the
   compatibility plus structured fields without writing to stdout.

Also inspect the state database read-only to confirm lifecycle rows exist and the complete source and
converted subtitle lines do not appear in SQLite.
