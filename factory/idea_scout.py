from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import config
from factory.contracts import utc_now_iso
from factory.idea_intake import (
    _extract_tags,
    _map_families,
    all_ideas,
    generated_ideas_path,
    idea_scout_state_path,
)
from pathlib import Path
from typing import Any, Dict, List


_SCOUT_QUERIES = [
    "funding rate trading strategy",
    "market regime trading strategy",
    "prediction market trading strategy",
]


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")) or {})
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _rss_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={urllib_parse.quote_plus(query)}"


def _fetch_feed_items(query: str, *, limit: int = 5) -> List[Dict[str, str]]:
    url = _rss_url(query)
    with urllib_request.urlopen(url, timeout=20) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    items: List[Dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        description = str(item.findtext("description") or "").strip()
        if not title or not link:
            continue
        items.append({"title": title, "link": link, "summary": description, "query": query})
    return items


def maybe_run_idea_scout(project_root: Path) -> Dict[str, Any]:
    if not bool(getattr(config, "FACTORY_IDEA_SCOUT_ENABLED", True)):
        return {"ran": False, "reason": "disabled"}
    state_path = idea_scout_state_path(project_root)
    generated_path = generated_ideas_path(project_root)
    state = _read_json(state_path)
    last_run = _parse_iso_dt(state.get("last_run_at"))
    interval_hours = max(1, int(getattr(config, "FACTORY_IDEA_SCOUT_INTERVAL_HOURS", 48) or 48))
    now = datetime.now(timezone.utc)
    if last_run is not None and (now - last_run) < timedelta(hours=interval_hours):
        return {"ran": False, "reason": "interval_not_elapsed"}

    existing = _read_json(generated_path).get("items") or []
    existing_rows = [dict(row) for row in existing if isinstance(row, dict)]
    seen_links = {str(item.get("source_url") or "") for item in existing_rows}
    seen_titles = {str(item.get("title") or "").lower() for item in all_ideas(project_root)}
    max_new = max(1, int(getattr(config, "FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN", 3) or 3))
    new_items: List[Dict[str, Any]] = []

    for query in _SCOUT_QUERIES:
        for item in _fetch_feed_items(query, limit=6):
            title = str(item.get("title") or "").strip()
            if not title or title.lower() in seen_titles or item["link"] in seen_links:
                continue
            idea_id = f"scout_{now.strftime('%Y%m%d')}_{len(existing_rows) + len(new_items) + 1:03d}"
            summary = str(item.get("summary") or "").strip()
            new_items.append(
                {
                    "idea_id": idea_id,
                    "rank": len(existing_rows) + len(new_items) + 1,
                    "title": title,
                    "summary": summary[:600],
                    "source": "scout",
                    "source_path": str(generated_path),
                    "source_url": item["link"],
                    "query": item["query"],
                    "family_candidates": _map_families(title, summary),
                    "tags": _extract_tags(title, summary),
                    "generated_at": utc_now_iso(),
                }
            )
            if len(new_items) >= max_new:
                break
        if len(new_items) >= max_new:
            break

    payload = {"items": existing_rows + new_items}
    _write_json(generated_path, payload)
    _write_json(
        state_path,
        {
            "last_run_at": utc_now_iso(),
            "last_new_count": len(new_items),
            "total_generated": len(payload["items"]),
        },
    )
    return {"ran": True, "new_count": len(new_items), "path": str(generated_path)}
