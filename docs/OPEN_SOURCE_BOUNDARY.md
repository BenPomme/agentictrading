# Open Source Boundary

This document defines what `agentictrading` exposes publicly and what remains private.

## What Is Public In This Repo

The public repository is intended to show and support:

- the paper-mode autonomous research factory
- the dashboard and operator-facing control-plane surfaces
- Meerkat and MobKit orchestration integration
- Goldfish provenance and experiment-memory integration
- deterministic gates, cost controls, and safety policies
- example strategy families and demo-safe workflows
- public docs that explain how the stack works

## What Stays Private

The public repository does not include:

- live-trading enablement
- venue secrets, certificates, and private credentials
- production deployment manifests and private infrastructure
- proprietary data pipelines or commercial-only connectors
- highest-alpha strategy families, heuristics, and production tuning
- private operator runbooks and internal escalation paths

## Why The Boundary Exists

The goal of the public repository is to make the architecture, workflows, and developer experience inspectable and usable without giving away the full production trading edge.

This repo is a flagship reference app for:

- [Meerkat](https://github.com/lukacf/meerkat)
- [meerkat-mobkit](https://github.com/lukacf/meerkat-mobkit)
- [Goldfish](https://github.com/lukacf/goldfish)

That means the public value is in showing a hard, realistic workload with clear safety and provenance boundaries, not in publishing every private operational advantage.

## Contributions We Welcome

We welcome contributions that improve:

- public documentation
- paper-mode workflows
- safety and governance logic
- dashboard usability
- observability and reproducibility
- integration robustness across Meerkat, MobKit, and Goldfish

## Contributions We May Decline

We may decline or redirect contributions that:

- try to turn the public repo into a live-trading product
- require private production context to review safely
- expose secrets, non-public infrastructure details, or proprietary datasets
- bypass the stated architecture boundaries

## License Alignment

The public stack intentionally uses different license layers:

- Meerkat: permissive (`MIT OR Apache-2.0`)
- MobKit: permissive (`MIT OR Apache-2.0`)
- Goldfish: reciprocal (`AGPL-3.0`)
- AgenticTrading: reciprocal (`AGPL-3.0`)

This keeps the lower-level agent infrastructure easy to adopt while preserving reciprocal obligations for the flagship reference app and provenance layer.
