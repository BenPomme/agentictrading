# NEBULA Factory & Dashboard — Operations Guide

This file is the single source of truth for starting, stopping, and
verifying the factory and dashboard. Every AI agent session that touches
this repo **must** read and follow this file before doing anything else.

---

## Prerequisites

All commands run from the repo root:
`/Users/benjaminpommeraud/Documents/AgenticTrading`

The dashboard serves a React build from `dashboard-ui/dist/`. If the
build is missing or stale, rebuild first (see "Rebuild" below).

### Critical `.env` settings

These three lines must be correct or the factory is broken:

```
FACTORY_AGENT_PROVIDER_ORDER=codex,openai_api,deterministic
OPENAI_API_KEY=sk-...    # REQUIRED for openai_api fallback
EXECUTION_REPO_ROOT=     # MUST be empty
```

If `OPENAI_API_KEY` is empty, the `openai_api` provider will fail and
all agent runs fall through to `deterministic` (no-op). The factory
will appear to run but produce no useful work.

If `FACTORY_AGENT_PROVIDER_ORDER` is missing `openai_api`, the same
thing happens when `codex` CLI is not installed.

---

## 1. Start

### Dashboard (port 8787)

```bash
python3 scripts/factory_dashboard.py --host 0.0.0.0 --port 8787
```

### Factory loop

```bash
python3 scripts/factory_loop.py --json
```

Both are long-running processes. Run them in background terminals or
with `&`. The factory loop auto-starts the data refresh scheduler as a
child process.

### Typical startup sequence

```bash
# 1. Check nothing is already running
lsof -i :8787 -t 2>/dev/null | xargs kill 2>/dev/null
ps aux | grep factory_loop | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
sleep 1

# 2. Start dashboard
python3 scripts/factory_dashboard.py --host 0.0.0.0 --port 8787 &

# 3. Start factory loop
python3 scripts/factory_loop.py --json &

# 4. Verify
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/
# Should print: 200
```

---

## 2. Stop

### Stop dashboard

```bash
lsof -i :8787 -t 2>/dev/null | xargs kill 2>/dev/null
```

### Stop factory loop (and its data refresh scheduler child)

```bash
ps aux | grep factory_loop | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
```

### Stop everything

```bash
lsof -i :8787 -t 2>/dev/null | xargs kill 2>/dev/null
ps aux | grep factory_loop | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
ps aux | grep data_refresh_scheduler | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
```

---

## 3. Rebuild the dashboard

Only needed when `dashboard-ui/src/` files have changed.

```bash
cd dashboard-ui && npm install && npm run build
```

This compiles TypeScript and bundles the React app into `dashboard-ui/dist/`.
The dashboard server serves static files from that directory.

If `tsconfig.app.json` or `src/vite-env.d.ts` are missing, the build
will fail. Both are tracked in git — if they are missing, your branch
is stale (see "Git hygiene" below).

---

## 4. Verify health

### Quick check (CLI)

```bash
curl -s http://127.0.0.1:8787/api/snapshot | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d.get('execution', {}).get('portfolios', []):
    pid = p.get('portfolio_id')
    print(f\"{pid}: status={p.get('display_status')} health={p.get('execution_health_status')} issues={p.get('execution_issue_codes')}\")
"
```

All portfolios should show `status=active health=healthy issues=[]`.

### Browser check

Navigate to `http://127.0.0.1:8787` and confirm:

- Title: "NEBULA Control Room"
- Status: ONLINE / FULL
- No `heartbeat_stale` alerts
- Portfolios count matches family count (currently 4/4)
- Families show LLM-generated names (not hardcoded legacy names like
  "Betfair Core", "Cascade Alpha", "Contrarian Legacy")

---

## 5. Git hygiene — critical for every session start

### Always check the bng worktree

The `nebula-agent-cost-guard` branch in the bng worktree
(`~/.cursor/worktrees/AgenticTrading/bng`) may have commits ahead of
`main` from parallel Codex CLI sessions.

```bash
# From the main repo
git log --oneline main..nebula-agent-cost-guard
```

If it shows commits, merge them:

```bash
git merge nebula-agent-cost-guard --ff-only
```

### Check for uncommitted work

```bash
# Main worktree
cd /Users/benjaminpommeraud/Documents/AgenticTrading && git status

# bng worktree
cd /Users/benjaminpommeraud/.cursor/worktrees/AgenticTrading/bng && git status
```

If either has uncommitted changes, commit them before proceeding.

---

## 6. What NOT to do

- **Never set `EXECUTION_REPO_ROOT`** in `.env`. This repo is fully
  standalone. All data lives in `data/`. All portfolio state lives in
  `data/portfolios/`. There is no external execution repo.
- **Never set `EXECUTION_PORTFOLIO_STATE_ROOT`** to an external path.
  It must be `data/portfolios` (relative to repo root) or left empty.
- **Never start the dashboard on port 8788**. The canonical port is
  **8787** (the default in `factory_dashboard.py`).
- **Never run the factory from an old commit** that has hardcoded
  template models. Verify with:
  `grep -q _design_model_for_family factory/orchestrator.py && echo OK`

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Portfolios show "degraded" / `heartbeat_stale` | Dashboard reading stale heartbeats | Check `.env` has `EXECUTION_PORTFOLIO_STATE_ROOT=data/portfolios` and `EXECUTION_REPO_ROOT=` (empty). Restart dashboard. |
| "Connection Failed" in browser | Two processes on same port, or dashboard not started | `lsof -i :8787` to check. Kill duplicates. Restart. |
| Build fails: "Cannot find module 'react'" | `node_modules` missing | `cd dashboard-ui && npm install && npm run build` |
| Build fails: "Cannot read tsconfig.app.json" | File missing — branch is stale | Merge `nebula-agent-cost-guard` into `main` |
| Agent runs all show "codex not found" + `deterministic` | `.env` missing `openai_api` in provider chain or missing `OPENAI_API_KEY` | Set `FACTORY_AGENT_PROVIDER_ORDER=codex,openai_api,deterministic` and add a valid `OPENAI_API_KEY=sk-...` to `.env`. **Restart factory loop** (it reads `.env` at startup only). |
| Agent runs show `openai_api` but fail | `OPENAI_API_KEY` empty or invalid | Add a valid key to `.env`. **Restart factory loop.** |
| Factory produces hardcoded template models | Running old code before "Eliminate hardcoded models" commit | Merge latest from `nebula-agent-cost-guard` |
| Factory loop refuses to start (exit 1) | `openai_api` in provider chain but `OPENAI_API_KEY` empty | The startup validator blocks this on purpose. Add a valid key to `.env`. |

### CRITICAL: Restart after `.env` changes

The factory loop reads `.env` **once at startup**. If you edit `.env`,
the running process still has stale values. You **MUST** kill and
restart the factory loop after any `.env` change:

```bash
ps aux | grep factory_loop | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
sleep 2
python3 scripts/factory_loop.py --json &
```
