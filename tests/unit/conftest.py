from __future__ import annotations

import sys
from pathlib import Path

import config


def execution_repo_root() -> Path | None:
    raw = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.exists():
        return None
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
