# Contributing

Thanks for contributing to AgenticTrading.

AgenticTrading is the public flagship reference app for the Meerkat + Goldfish stack. The goal of this repository is to make the paper-mode research factory understandable, runnable, and improvable by external contributors without exposing production secrets or live-trading infrastructure.

## Before You Start

- Read [README.md](README.md) for project scope and safety boundaries.
- Read [docs/OPEN_SOURCE_BOUNDARY.md](docs/OPEN_SOURCE_BOUNDARY.md) to understand what belongs in the public repo and what stays private.
- If your change touches the ongoing runtime/provenance migration, read:
  - [docs/refactor/CODEX_REFRACTOR_OVERVIEW.md](docs/refactor/CODEX_REFRACTOR_OVERVIEW.md)
  - [docs/refactor/IMPLEMENTATION_PLAN_DETAILED.md](docs/refactor/IMPLEMENTATION_PLAN_DETAILED.md)
  - [docs/refactor/COST_CONTROL_AND_MULTIAGENT_POLICY.md](docs/refactor/COST_CONTROL_AND_MULTIAGENT_POLICY.md)
  - [docs/refactor/ACCEPTANCE_TESTS_AND_DOD.md](docs/refactor/ACCEPTANCE_TESTS_AND_DOD.md)

## Scope

Good public contributions include:

- paper-mode research workflows
- deterministic safety and governance logic
- dashboard and observability improvements
- developer experience and docs
- Meerkat, MobKit, and Goldfish integration hardening
- example families, safe fixtures, and demo-ready workflows

Out of scope for this public repo:

- live trading enablement
- venue credentials, certificates, or secrets
- production deployment manifests
- proprietary connectors or datasets
- private alpha-generating strategy logic

## Development Principles

- Keep changes small and reviewable.
- Preserve backward compatibility unless the change explicitly removes deprecated behavior.
- Keep architecture boundaries explicit:
  - business logic depends on repo-local interfaces
  - orchestration goes through the runtime adapter
  - provenance goes through Goldfish
  - governance stays in governance modules
- Do not introduce hidden direct provider calls in business logic.
- Do not bypass safety gates or turn on live trading.

## Pull Requests

For code changes:

- include tests for behavior changes where practical
- update docs if the public interface or operator behavior changes
- explain risks, assumptions, and rollback behavior in the PR description

For documentation-only changes:

- keep claims precise
- avoid implying live trading support in the open repo

## Reporting Bugs

- Use GitHub Issues for reproducible bugs and feature requests.
- Use [SUPPORT.md](SUPPORT.md) for usage questions.
- Use [SECURITY.md](SECURITY.md) for vulnerability reporting. Do not open public issues for security problems.

## Review Process

This repo currently uses a maintainer-led review model. Maintainers may ask contributors to narrow scope, split changes, or move private/production-sensitive work out of the public repository.

## Licensing

By contributing to this repository, you agree that your contributions will be licensed under the repository license: GNU Affero General Public License v3.0.
