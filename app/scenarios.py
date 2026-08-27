"""
Load and validate authored courtroom scenario packs from content/scenarios/.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.models import Case, Counsel, Evidence

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "content" / "scenarios"
_ALLOWED_REVEAL = frozenset({"round_gte", "keyword"})
_ALLOWED_VERDICTS = frozenset({"guilty", "not_guilty"})

_SCENARIO_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _safe_scenario_path(scenario_id: str) -> Path:
    """Resolve scenario file under scenarios dir; reject traversal / bad ids."""
    if not _SCENARIO_ID_RE.fullmatch(scenario_id or ""):
        raise ValueError(f"invalid scenario_id: {scenario_id!r}")
    root = _SCENARIOS_DIR.resolve()
    path = (root / f"{scenario_id}.json").resolve()
    try:
        path.relative_to(root)
    except ValueError as e:
        raise ValueError(f"invalid scenario_id: {scenario_id!r}") from e
    return path



def _validate_counsel(role: str, data: dict) -> None:
    if not data.get("id") or not data.get("name") or not data.get("persona"):
        raise ValueError(f"{role} missing id/name/persona")
    brief = data.get("brief") or []
    if not brief or not all(isinstance(b, str) and b.strip() for b in brief):
        raise ValueError(f"{role} has empty brief")


def validate_scenario(data: dict) -> None:
    required = [
        "id", "level", "title", "charge", "setting", "case_summary",
        "defendant", "verdict_truth", "solution", "prosecution", "defense", "evidence",
    ]
    for key in required:
        if key not in data:
            raise ValueError(f"scenario missing {key}")

    if data["verdict_truth"] not in _ALLOWED_VERDICTS:
        raise ValueError(f"invalid verdict_truth {data['verdict_truth']!r}")
    if not data.get("solution") or not str(data["solution"]).strip():
        raise ValueError("solution is empty")

    defendant = data["defendant"]
    if not defendant.get("id") or not defendant.get("name"):
        raise ValueError("defendant needs id and name")

    _validate_counsel("prosecution", data["prosecution"])
    _validate_counsel("defense", data["defense"])

    evidence = data.get("evidence") or []
    if not evidence:
        raise ValueError("scenario has no evidence")
    for item in evidence:
        cond = item.get("reveal_condition") or {}
        if cond.get("type") not in _ALLOWED_REVEAL:
            raise ValueError(
                f"evidence {item.get('id')!r} has unsupported reveal type {cond.get('type')!r}"
            )


def _to_case(data: dict) -> Case:
    prosecution = Counsel(**data["prosecution"])
    defense = Counsel(**data["defense"])
    evidence = [Evidence(**e) for e in data["evidence"]]
    return Case(
        scenario_id=data["id"],
        level=int(data["level"]),
        title=data["title"],
        charge=data["charge"],
        setting=data["setting"],
        case_summary=data["case_summary"],
        defendant=dict(data["defendant"]),
        verdict_truth=data["verdict_truth"],
        solution=data["solution"],
        prosecution=prosecution,
        defense=defense,
        evidence=evidence,
    )


def list_scenarios() -> list[dict[str, Any]]:
    """Public summaries for level picker — no secrets."""
    summaries = []
    for path in sorted(_SCENARIOS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_scenario(data)
        summaries.append({
            "id": data["id"],
            "level": data["level"],
            "title": data["title"],
            "setting": data["setting"],
            "case_summary": data["case_summary"],
        })
    summaries.sort(key=lambda s: s["level"])
    return summaries


def load_scenario(scenario_id: str) -> Case:
    path = _safe_scenario_path(scenario_id)
    if not path.exists():
        # also allow lookup by id field inside files
        for candidate in _SCENARIOS_DIR.glob("*.json"):
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if data.get("id") == scenario_id:
                validate_scenario(data)
                return _to_case(data)
        raise FileNotFoundError(f"scenario not found: {scenario_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_scenario(data)
    return _to_case(data)


def public_counsel(counsel: Counsel) -> dict:
    return {"id": counsel["id"], "name": counsel["name"], "persona": counsel["persona"]}


def public_case_view(case: Case) -> dict:
    """Safe projection for lobby / create response."""
    return {
        "scenario_id": case["scenario_id"],
        "level": case["level"],
        "title": case["title"],
        "charge": case["charge"],
        "setting": case["setting"],
        "case_summary": case["case_summary"],
        "defendant": case["defendant"],
        "prosecution": public_counsel(case["prosecution"]),
        "defense": public_counsel(case["defense"]),
    }
