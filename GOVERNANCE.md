# Governance

## Project Model

AgenticTrading currently uses a maintainer-led governance model.

Maintainers are responsible for:

- roadmap direction
- architecture boundary decisions
- release readiness
- community moderation
- licensing and policy decisions for the public repository

## Decision Principles

When making changes, maintainers prioritize:

- paper-mode safety over growth in scope
- explicit architecture boundaries over ad hoc shortcuts
- reproducibility and provenance over convenience
- bounded cost and deterministic gates over unconstrained autonomy
- public clarity over exposing private trading edge

## Public Repository Boundaries

This repository is the public flagship reference app for the Meerkat + Goldfish stack. It is not a promise to open every private strategy, dataset, operator flow, or production deployment system.

The public repo focuses on:

- the paper-mode research factory
- the control plane and dashboard
- orchestration and provenance integration
- safe example workflows and public documentation

## Maintainer Rights

Maintainers may:

- reject or defer changes that widen scope beyond the public boundary
- require security-sensitive or proprietary work to stay private
- ask contributors to split broad changes into smaller patches
- close issues that request live-trading enablement for the public repo

## Changes To Governance

This document may evolve as the contributor base grows, but until a broader governance structure is established, repository governance remains maintainer-led.
