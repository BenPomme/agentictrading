# Security Policy

## Supported Use

This public repository is intended for paper-mode research, orchestration, provenance, and operator tooling. It is not a public live-trading product.

## Reporting a Vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Preferred reporting path:

1. Open a private GitHub security advisory if private vulnerability reporting is enabled for this repository.
2. If that is not available, contact the maintainer privately through the repository owner profile on GitHub and include `SECURITY` in the subject or opening line.

Include:

- affected component or file path
- impact and exploitability
- reproduction steps or proof of concept
- any suggested mitigation

## What To Avoid Reporting Publicly

Do not publish:

- credentials, keys, tokens, or certificates
- exploit details before a fix is available
- venue-specific operational details that could be abused
- production environment assumptions that are not already public

## Scope Notes

High-priority issues include:

- authentication or authorization flaws
- privilege escalation in operator surfaces
- remote code execution paths
- unsafe secret handling
- hidden live-trading activation paths
- unsafe provenance or runtime boundary bypasses

## Response Expectations

The project uses a maintainer-led process. Triage, remediation timeline, and disclosure timing depend on severity and maintainer availability.

## Hard Boundary

This public repository must not be treated as permission to expose live-trading credentials, internal deployment details, or private production systems.
