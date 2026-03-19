#!/usr/bin/env python3
"""
Lightweight daemon that runs data refresh scripts on schedule alongside the factory loop.
"""

import argparse
import json
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.paper_data import build_refresh_plan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "factory" / "data_refresh_state.json"

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
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


def run_script(name, script_path, args=None, timeout=300):
    path = PROJECT_ROOT / script_path
    if not path.exists():
        logger.error("Script not found: %s", path)
        return False
    logger.info("%s refresh started at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        result = subprocess.run(
            [sys.executable, str(path), *(args or [])],
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("%s refresh completed at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
            return True
        logger.error(
            "%s refresh failed at %s: exit code %s, stderr=%s",
            name,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            result.returncode,
            (result.stderr[:500] if result.stderr else ""),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("%s refresh timed out at %s", name, time.strftime("%Y-%m-%d %H:%M:%S"))
        return False
    except Exception as e:
        logger.error("%s refresh raised exception at %s: %s", name, time.strftime("%Y-%m-%d %H:%M:%S"), e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Data refresh scheduler daemon")
    parser.add_argument("--check-interval-seconds", type=int, default=30)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    state = load_state()
    plan = build_refresh_plan(PROJECT_ROOT)
    tasks = {item.task_id: item for item in plan}
    for name in tasks:
        state.setdefault(name, 0)

    for name, task in tasks.items():
        logger.info("%s refresh (first run on startup)", name)
        run_script(name, task.script, args=task.args)
        state[name] = time.time()
    save_state(state)

    while not _shutdown:
        now = time.time()
        plan = build_refresh_plan(PROJECT_ROOT)
        tasks = {item.task_id: item for item in plan}
        for name in list(state.keys()):
            if name not in tasks:
                state.pop(name, None)
        for name, task in tasks.items():
            state.setdefault(name, 0)
            interval = task.interval_seconds
            last = state.get(name, 0)
            if now - last >= interval:
                if run_script(name, task.script, args=task.args):
                    state[name] = now
                    save_state(state)
        time.sleep(args.check_interval_seconds)

    logger.info("Scheduler shutting down gracefully")


if __name__ == "__main__":
    main()
