# GitHub Repo Setup

This checklist captures the GitHub-side settings required to make the public repository match the repo contents.

## Recommended Repository Metadata

- Description:
  `Autonomous trading research factory and flagship reference app for Meerkat + Goldfish`
- Homepage:
  leave unset for now unless a stable public docs or landing page is available
- Topics:
  `agents`, `agentic`, `multi-agent`, `llm`, `research`, `paper-trading`, `mlops`, `provenance`, `meerkat`, `goldfish`, `mobkit`, `python`

## Recommended Settings

- Visibility: public
- Default branch: `main`
- Issues: enabled
- Discussions: enabled
- Forking: enabled
- Wiki: optional
- Projects: optional

## Community Health Expectations

After pushing the repo-side files added in this pass, GitHub should detect:

- `README.md`
- `LICENSE`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `.github` issue templates
- `.github/PULL_REQUEST_TEMPLATE.md`

Expected improvement:

- community profile should increase materially from the previous 14%
- license should resolve to `AGPL-3.0`

## Manual Verification Commands

Use these read-only API checks after pushing:

```bash
curl -s https://api.github.com/repos/BenPomme/agentictrading | jq '{description, homepage, topics, has_discussions, license:(.license.spdx_id // null)}'
curl -s https://api.github.com/repos/BenPomme/agentictrading/community/profile | jq '{health_percentage, files}'
```

## Notes

This environment did not have authenticated GitHub CLI access available, so the remote settings above could not be applied directly from here.
