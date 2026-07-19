# Development and release checks

[中文](zh/development.md) · [Documentation home](../README.md)

Use Conda base and install the complete project in editable mode:

```bash
conda activate base
cd /path/to/bmlsub
python -m pip install -e .
```

## Source checks

The public GitHub repository includes the automated regression tests in `tests/` and the reusable maintenance utilities in `tools/`. Private validation media, local credentials, generated state, and operator-specific automation are not published. Before a release, run the repository tests and then validate the installed public package directly:

```bash
python -m unittest discover -s tests
python -m compileall -q bmlsub
bmlsub --version
bmlsub --help
bmlsub workstation series create --help
bmlsub workstation delivery --help
```

Exercise the installed CLI and public Python API in temporary workspaces. At minimum verify series creation, default refusal to overwrite, explicit replacement, series discovery from a numeric episode directory, subtitle conversion/reuse, Run query, and machine-readable stdout.

## Packaging checks

Build both distribution formats and inspect their contents before upload:

```bash
python -m build
python -m zipfile -l dist/*.whl
```

The wheel must contain only package source and distribution metadata. The sdist may additionally contain the repository documentation, `tests/`, and `tools/`, but neither archive may contain `.claude/`, build caches, local databases, credentials, media, receipts, or private validation paths. Install the built wheel into a clean environment and repeat the CLI smoke checks.

## Repository hygiene

`.gitignore` excludes Python/build caches, local state/log/backups, credential/env/key files, media, torrents, receipts, analyses, and fonts. Before publication, scan for:

- `.DS_Store`, `__pycache__`, egg-info, build/dist leftovers;
- SQLite/database/log files and generated media;
- private key blocks or credential-like values;
- absolute home paths, private host aliases, and project-specific validation names;
- unexpected files larger than 1 MB;
- broken relative Markdown links.

New Stages should continue to use StageRunner, actual `stage_inputs`, Artifact writers, argv-only ProcessRunner, strict normalized Profiles, and shared CLI/Pipeline business implementations. External release smoke checks must use fake clients or explicit bounded read-only probes unless the operator separately confirms a real side effect.
