#!/usr/bin/env python3
"""
Lightweight daemon that runs data refresh scripts on schedule alongside the factory loop.
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.paper_data import build_refresh_plan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "factory" / "data_refresh_state.json"
PID_FILE = PROJECT_ROOT / "data" / "factory" / "data_refresh_scheduler.pid"

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True


def _preferred_python() -> str:
    override = str(os.environ.get("FACTORY_REFRESH_PYTHON") or "").strip()
    if override:
        return override
    preferred = PROJECT_ROOT / ".venv312" / "bin" / "python"
    if preferred.exists():
        return str(preferred)
    return sys.executable


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                raw = json.load(f)
            normalized = {}
            for task_id, payload in dict(raw or {}).items():
                if isinstance(payload, dict):
                    normalized[str(task_id)] = dict(payload)
                elif isinstance(payload, (int, float)):
                    normalized[str(task_id)] = {"last_success_at": float(payload)}
            return normalized
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load state from %s: %s", STATE_FILE, e)
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        logger.warning("Could not save state to %s: %s", STATE_FILE, e)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _state_entry(state, task):
    entry = dict(state.get(task.task_id) or {})
    entry.setdefault("task_id", task.task_id)
    entry.setdefault("script", task.script)
    entry.setdefault("args", list(task.args))
    entry.setdefault("interval_seconds", int(task.interval_seconds))
    entry.setdefault("feed_type", task.feed_type)
    entry.setdefault("source", task.source)
    entry.setdefault("last_success_at", None)
    entry.setdefault("last_failure_at", None)
    entry.setdefault("last_started_at", None)
    entry.setdefault("last_error", "")
    entry.setdefault("missed_runs", 0)
    return entry


def run_script(name, script_path, args=None, timeout=300):
    path = PROJECT_ROOT / script_path
    if not path.exists():
        logger.error("Script not found: %s", path)
        return False, f"script not found: {path}"
    python_executable = _preferred_python()
    logger.info("%s refresh started at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        result = subprocess.run(
            [python_executable, str(path), *(args or [])],
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("%s refresh completed at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
            return True, ""
        logger.error(
            "%s refresh failed at %s: exit code %s, stderr=%s",
            name,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            result.returncode,
            (result.stderr[:500] if result.stderr else ""),
        )
        return False, (result.stderr[:500] if result.stderr else f"exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        logger.error("%s refresh timed out at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
        return False, "timeout"
    except Exception as e:
        logger.error("%s refresh raised exception at %s: %s", name, time.strftime("%Y-%m-%d %H:%M:%S"), e)
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Data refresh scheduler daemon")
    parser.add_argument("--check-interval-seconds", type=int, default=30)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    state = load_state()
    plan = build_refresh_plan(PROJECT_ROOT)
    tasks = {item.task_id: item for item in plan}
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    for task in tasks.values():
        state[task.task_id] = _state_entry(state, task)

    for name, task in tasks.items():
        logger.info("%s refresh (first run on startup)", name)
        entry = _state_entry(state, task)
        entry["last_started_at"] = _now_iso()
        ok, err = run_script(name, task.script, args=task.args)
        if ok:
            entry["last_success_at"] = _now_iso()
            entry["last_error"] = ""
        else:
            entry["last_failure_at"] = _now_iso()
            entry["last_error"] = err
        state[name] = entry
    save_state(state)

    try:
        while not _shutdown:
            now = time.time()
            plan = build_refresh_plan(PROJECT_ROOT)
            tasks = {item.task_id: item for item in plan}
            for name in list(state.keys()):
                if name not in tasks:
                    state.pop(name, None)
            for name, task in tasks.items():
                entry = _state_entry(state, task)
                last_success = entry.get("last_success_at")
                if isinstance(last_success, (int, float)):
                    last_success_epoch = float(last_success)
                elif isinstance(last_success, str) and last_success:
                    try:
                        last_success_epoch = datetime.fromisoformat(last_success.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
                    except ValueError:
                        last_success_epoch = 0.0
                else:
                    last_success_epoch = 0.0
                interval = int(task.interval_seconds)
                overdue_seconds = max(0.0, now - last_success_epoch - interval) if last_success_epoch else interval
                entry["missed_runs"] = int(overdue_seconds // max(interval, 1)) if last_success_epoch else 0
                if not last_success_epoch or now - last_success_epoch >= interval:
                    entry["last_started_at"] = _now_iso()
                    ok, err = run_script(name, task.script, args=task.args)
                    if ok:
                        entry["last_success_at"] = _now_iso()
                        entry["last_error"] = ""
                        entry["missed_runs"] = 0
                    else:
                        entry["last_failure_at"] = _now_iso()
                        entry["last_error"] = err
                state[name] = entry
                save_state(state)
            time.sleep(args.check_interval_seconds)
    finally:
        logger.info("Scheduler shutting down gracefully")
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
