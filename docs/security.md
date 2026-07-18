# Security (implemented boundaries)

[中文](zh/security.md) · [Documentation home](../README.md)

Diagnostic context, Artifact metadata, and ProductionRequest parameters recursively reject common secret-like key names. This prevents normal secret fields from entering state but cannot identify arbitrary sensitive values hidden under unrelated names.

R2/qB/Anibt secrets belong in Keychain or explicitly selected compatibility env/0600 files. SSH private keys remain with OpenSSH/agent/system management; VPS rclone secrets remain on the server.

CLI parsing requires external-action confirmation for credential probes and all external release commands. Python APIs do not provide that UI confirmation and require the embedding application to do so.

Formal output uses candidate validation, fsync, backup, and atomic replacement. File commit and SQLite registration are separate; registration failure is a failed Stage, not reusable success. No general cross-process output lock exists.

The default subtitle HTTP provider has no automatic retry/backoff. Probes are read-only but access real services. Upload/pull/seed/publish have real side effects. No delete/withdraw Stage exists for R2, remote files, qB tasks, or Anibt releases.

Repository hygiene is enforced by ignore/export rules and a release-time public-tree scan. Caches, local state, credentials, generated media, receipts, analyses, and fonts must remain outside the uploaded tree. Maintainers check for private identifiers, key blocks, unexpected large files, and generated artifacts before building distributions.
