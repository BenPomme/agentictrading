from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import config


_IDEA_START_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")

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


def _clean_line(line: str) -> str:
    text = " ".join(line.replace('"', "").split()).strip()
    return text


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
            title = _clean_line(match.group(2))
            current = {
                "idea_id": f"idea_{number:03d}",
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
        if not related:
            if row.get("family_candidates"):
                status = "adapted"
            else:
                status = "new"
        elif any(item.get("current_stage") in {"canary_ready", "live_ready", "approved_live"} for item in related):
            status = "promoted"
        elif all(not item.get("active", True) for item in related):
            status = "rejected"
        else:
            status = "tested"
        row["status"] = status
        row["lineage_count"] = len(related)
        row["family_count"] = len({item.get("family_id") for item in related if item.get("family_id")})
        annotated.append(row)
    return annotated


def split_active_and_archived_ideas(ideas: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    rows = list(ideas)
    archived_statuses = {"tested", "promoted", "rejected"}
    active = [row for row in rows if str(row.get("status") or "") not in archived_statuses]
    archived = [row for row in rows if str(row.get("status") or "") in archived_statuses]
    return {"active": active, "archived": archived}
