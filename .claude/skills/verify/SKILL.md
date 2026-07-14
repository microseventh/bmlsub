---
name: verify
summary: Verify bmlsub CLI behavior through its terminal surface
---

# Verify bmlsub

Run from the package directory with the conda base environment.

## Launch

Without installing the editable package:

```bash
conda run -n base env PYTHONPATH="$PWD" python -m bmlsub.cli --help
```

After editable install, also verify the console script with `bmlsub --help`.

## Safe flows

Use a fresh temporary directory and drive read-only/error paths:

```bash
conda run -n base env PYTHONPATH="$PWD" python -m bmlsub.cli inspect-episode --episode-dir "$TMPDIR" --episode-id 01
conda run -n base env PYTHONPATH="$PWD" python -m bmlsub.cli workstation inspect --root-dir "$TMPDIR" --episodes 01-03
conda run -n base env PYTHONPATH="$PWD" python -m bmlsub.cli encode --episode-dir "$TMPDIR" --episode-id 01
```

Expected: successful commands emit JSON to stdout; missing-source encode emits JSON error to stderr and exits 1. Do not exercise upload, seed, conversion, encoding, or packaging against real targets during routine verification.
