# Security

This is a single-user local application. Keep it bound to loopback. It has no login, shared-workspace authorization or public deployment hardening; do not expose it through a public reverse proxy or router port forwarding.

Only process trusted media, and keep decoding dependencies up to date. The optional model downloader uses fixed official URLs and checksums. Do not substitute untrusted checkpoints.

Personal media, generated videos, model weights, local configuration and caches are excluded from the repository. Local metadata can contain full paths. Review files before sharing them.

Avoid posting exploit details or private files in public issues. Use GitHub private vulnerability reporting if enabled, or contact the repository owner privately first.
