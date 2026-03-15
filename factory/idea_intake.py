from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import config


_IDEA_START_RE = re.compile(r"^\s*(\d+)\s*[\.:]\s*(.+?)\s*$")

_FAMILY_KEYWORDS = {
    "binance_cascade_regime": ["regime", "classifier", "pre-market", "volatility", "skew"],
    "binance_funding_contrarian": ["probability", "strike", "quantitative", "systematic", "income"],
    "polymarket_cross_venue": ["event", "earnings", "announcement", "probability"],
    "betfair_prediction_value_league": ["probability", "selection", "framework"],
    "betfair_information_lag": ["pre-market", "analyzer", "daily", "calendar"],
}

_VENUE_KEYWORDS = {
    "equity_options": ["option", "options", "0dte", "spx", "spy", "theta", "iron condor", "strike", "volatility", "earnings"],
    "crypto_funding": ["regime", "systematic", "quantitative", "probability"],
    "prediction_markets": ["probability", "event", "earnings", "announcement"],
}

_FAMILY_IDEA_TAG_PREFERENCES = {
    "binance_cascade_regime": {"prefer": {"crypto_funding"}, "avoid_if_only": {"equity_options"}},
    "binance_funding_contrarian": {"prefer": {"crypto_funding"}, "avoid_if_only": {"equity_options"}},
    "polymarket_cross_venue": {"prefer": {"prediction_markets", "risk"}, "avoid_if_only": {"equity_options"}},
    "betfair_prediction_value_league": {"prefer": {"prediction_markets"}, "avoid_if_only": {"equity_options"}},
    "betfair_information_lag": {"prefer": {"prediction_markets"}, "avoid_if_only": {"equity_options"}},
}

_NOISE_LINES = {"voir plus", "clara bennett", "@codeswithclara", "14h", "·", "i need a", "i"}
_ARCHIVED_IDEA_STATUSES = {"incubated", "tested", "promoted", "rejected"}
_ACTIVE_STATUS_PRIORITY = {"new": 0, "adapted": 1}
_ARCHIVED_STATUS_PRIORITY = {"incubated": 0, "tested": 1, "promoted": 2, "rejected": 3}


def ideas_path(project_root: Path) -> Path:
    for name in ("ideas.md", "IDEAS.md"):
        path = project_root / name
        if path.exists():
            return path
    return project_root / "ideas.md"


def _factory_root(project_root: Path) -> Path:
    configured = Path(str(getattr(config, "FACTORY_ROOT", "data/factory")))
    if configured.is_absolute():
        return configured
    return project_root / configured


def generated_ideas_path(project_root: Path) -> Path:
    return _factory_root(project_root) / "ideas" / "generated_ideas.json"


def idea_scout_state_path(project_root: Path) -> Path:
    return _factory_root(project_root) / "ideas" / "scout_state.json"


def manual_idea_watch_state_path(project_root: Path) -> Path:
    return _factory_root(project_root) / "ideas" / "manual_watch_state.json"


def _clean_line(line: str) -> str:
    text = " ".join(line.replace('"', "").split()).strip()
    return text


def _normalize_idea_title(text: str) -> str:
    cleaned = _clean_line(text)
    return re.sub(r"^(?:new\s+idea\s*:\s*)", "", cleaned, flags=re.IGNORECASE).strip()


def _is_noise_line(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    return lowered in _NOISE_LINES


def _extract_tags(*parts: str) -> List[str]:
    text = " ".join(parts).lower()
    tags: List[str] = []
    for venue, keywords in _VENUE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(venue)
    if "risk" in text or "risk management" in text:
        tags.append("risk")
    if "income" in text or "premium" in text:
        tags.append("income")
    return sorted(dict.fromkeys(tags))


def _map_families(title: str, summary: str) -> List[str]:
    text = f"{title} {summary}".lower()
    matches: List[str] = []
    for family_id, keywords in _FAMILY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matches.append(family_id)
    return matches


def parse_ideas_markdown(project_root: Path) -> List[Dict[str, Any]]:
    path = ideas_path(project_root)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    ideas: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    body_lines: List[str] = []
    seen_ids: Dict[str, int] = {}
    for raw_line in lines:
        match = _IDEA_START_RE.match(raw_line)
        if match:
            if current is not None:
                summary = " ".join(body_lines[:4]).strip()
                current["summary"] = summary
                current["family_candidates"] = _map_families(current["title"], summary)
                current["tags"] = _extract_tags(current["title"], summary)
                current["source"] = "manual"
                ideas.append(current)
            number = int(match.group(1))
            title = _normalize_idea_title(match.group(2))
            base_idea_id = f"idea_{number:03d}"
            ordinal = seen_ids.get(base_idea_id, 0) + 1
            seen_ids[base_idea_id] = ordinal
            idea_id = base_idea_id if ordinal == 1 else f"{base_idea_id}_{ordinal}"
            current = {
                "idea_id": idea_id,
                "rank": number,
                "title": title,
                "summary": "",
                "source_path": str(path),
            }
            body_lines = []
            continue
        if current is None:
            continue
        cleaned = _clean_line(raw_line)
        if _is_noise_line(cleaned):
            continue
        body_lines.append(cleaned)
    if current is not None:
        summary = " ".join(body_lines[:4]).strip()
        current["summary"] = summary
        current["family_candidates"] = _map_families(current["title"], summary)
        current["tags"] = _extract_tags(current["title"], summary)
        current["source"] = "manual"
        ideas.append(current)
    return ideas


def load_generated_ideas(project_root: Path) -> List[Dict[str, Any]]:
    path = generated_ideas_path(project_root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = list(payload.get("items") or []) if isinstance(payload, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def all_ideas(project_root: Path) -> List[Dict[str, Any]]:
    rows = parse_ideas_markdown(project_root) + load_generated_ideas(project_root)
    return sorted(rows, key=lambda row: (str(row.get("source") or "manual"), int(row.get("rank") or 9999), str(row.get("idea_id") or "")))


def _idea_priority_for_family(idea: Dict[str, Any], family_id: str) -> tuple[int, int, int, int]:
    tags = set(str(item) for item in (idea.get("tags") or []))
    family_candidates = set(str(item) for item in (idea.get("family_candidates") or []))
    prefs = _FAMILY_IDEA_TAG_PREFERENCES.get(family_id, {})
    prefer = set(prefs.get("prefer") or set())
    avoid_if_only = set(prefs.get("avoid_if_only") or set())
    direct_match = 0 if family_id in family_candidates else 1
    preferred_tag_rank = 0 if tags.intersection(prefer) else 1
    avoid_rank = 1 if tags and tags.issubset(avoid_if_only) else 0
    return (avoid_rank, direct_match, preferred_tag_rank, int(idea.get("rank") or 999))


def relevant_ideas_for_family(
    project_root: Path,
    family_id: str,
    *,
    limit: int = 3,
    existing_lineages: Iterable[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    rows = all_ideas(project_root)
    lineage_rows = list(existing_lineages or [])
    used_idea_ids = set()
    for item in lineage_rows:
        if isinstance(item, dict):
            idea_id = str(item.get("source_idea_id") or "").strip()
        else:
            idea_id = str(getattr(item, "source_idea_id", "") or "").strip()
        if idea_id:
            used_idea_ids.add(idea_id)
    candidates = [row for row in rows if family_id in list(row.get("family_candidates") or [])]
    if not candidates:
        candidates = rows
    candidates = sorted(candidates, key=lambda row: _idea_priority_for_family(row, family_id))
    unused = [row for row in candidates if str(row.get("idea_id") or "") not in used_idea_ids]
    ranked = unused + [row for row in candidates if row not in unused]
    filtered = [row for row in ranked if _idea_priority_for_family(row, family_id)[0] == 0]
    return (filtered or ranked)[:limit]


def annotate_idea_statuses(ideas: Iterable[Dict[str, Any]], lineages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lineage_rows = list(lineages)
    annotated: List[Dict[str, Any]] = []
    for idea in ideas:
        row = dict(idea)
        related = [item for item in lineage_rows if item.get("source_idea_id") == row.get("idea_id")]
        incubated = any(str(item.get("creation_kind") or "").strip() == "new_model" for item in related)
        if not related:
            status = "new"
        elif any(item.get("current_stage") in {"canary_ready", "live_ready", "approved_live"} for item in related):
            status = "promoted"
        elif incubated:
            status = "incubated"
        elif all(not item.get("active", True) for item in related):
            status = "rejected"
        else:
            status = "tested"
        row["status"] = status
        row["lineage_count"] = len(related)
        row["family_count"] = len({item.get("family_id") for item in related if item.get("family_id")})
        row["related_lineage_ids"] = [str(item.get("lineage_id") or "") for item in related if item.get("lineage_id")]
        annotated.append(row)
    return annotated


def _idea_sort_key(row: Dict[str, Any], *, archived: bool) -> tuple[Any, ...]:
    status = str(row.get("status") or "")
    source = str(row.get("source") or "")
    rank = int(row.get("rank") or 0)
    title = str(row.get("title") or row.get("idea_id") or "")
    if archived:
        return (_ARCHIVED_STATUS_PRIORITY.get(status, 9), 0 if source == "manual" else 1, -rank, title)
    return (_ACTIVE_STATUS_PRIORITY.get(status, 9), 0 if source == "manual" else 1, -rank, title)


def split_active_and_archived_ideas(ideas: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    rows = list(ideas)
    active = [row for row in rows if str(row.get("status") or "") not in _ARCHIVED_IDEA_STATUSES]
    archived = [row for row in rows if str(row.get("status") or "") in _ARCHIVED_IDEA_STATUSES]
    return {
        "active": sorted(active, key=lambda row: _idea_sort_key(row, archived=False)),
        "archived": sorted(archived, key=lambda row: _idea_sort_key(row, archived=True)),
    }


def _idea_watch_key(idea: Dict[str, Any]) -> str:
    payload = "|".join(
        [
            str(idea.get("idea_id") or "").strip(),
            str(idea.get("title") or "").strip(),
            str(idea.get("summary") or "").strip(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def maybe_run_manual_idea_watch(project_root: Path) -> Dict[str, Any]:
    path = ideas_path(project_root)
    if not path.exists():
        return {"ran": False, "reason": "missing_ideas_file"}
    state_path = manual_idea_watch_state_path(project_root)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    previous = {str(item) for item in (state.get("seen_keys") or []) if str(item).strip()}
    rows = parse_ideas_markdown(project_root)
    seen_rows = []
    seen_keys: List[str] = []
    for row in rows:
        key = _idea_watch_key(row)
        seen_keys.append(key)
        if key not in previous:
            seen_rows.append(
                {
                    "idea_id": row.get("idea_id"),
                    "rank": row.get("rank"),
                    "title": row.get("title"),
                }
            )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "last_read_at": rows and path.stat().st_mtime_ns,
                "last_seen_path": str(path),
                "idea_count": len(rows),
                "seen_keys": seen_keys,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "ran": True,
        "path": str(path),
        "idea_count": len(rows),
        "new_count": len(seen_rows),
        "new_items": seen_rows,
    }
