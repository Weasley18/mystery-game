"""
Game-master agent: generates a full case (victim, killer, suspects, clues)
once, at game creation. This runs as a one-shot call, not part of the
per-turn graph -- there's no reason to pay LangGraph's checkpointing
overhead for something that happens exactly once per game.

Provider and model selection live in app.llm -- this file owns the prompt
and validation.
"""

import json
import re
import uuid

from app.llm import get_gm_llm
from app.models import Case, Suspect, Clue

_ALLOWED_REVEAL_TYPES = frozenset({"round_gte", "keyword"})

_CASE_GENERATION_PROMPT = """\
You are a mystery writer designing a murder mystery party game.

Generate a self-contained case with:
- A victim and setting (theme: {theme})
- Exactly {num_suspects} suspects, each with a name, a distinct persona/speech
  style, a backstory (relationship to victim + alibi), and 3-5 private facts
  they know and must never contradict.
- Exactly one suspect is the killer. Their "facts" list must include their
  real motive, worded so it can be used to keep their lies consistent.
- 4-6 clues, each with a reveal_condition. reveal_condition.type MUST be
  exactly one of: "round_gte" (value = integer as string, e.g. "2") or
  "keyword" (value = a lowercase word/phrase that triggers the clue when
  it appears in a player's question).
- A solution paragraph explaining exactly how the killer did it, for reveal
  at game end.

Respond as JSON only (no markdown fences), matching this shape:
{{
  "victim": str, "setting": str, "killer_id": str, "motive": str, "solution": str,
  "suspects": {{"<id>": {{"id": str, "name": str, "persona": str, "backstory": str,
                "is_killer": bool, "facts": [str, ...]}}, ...}},
  "clues": [{{"id": str, "text": str, "reveal_condition": {{"type": str, "value": str}},
              "revealed": false}}, ...]
}}
"""


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _content_text(response) -> str:
    content = response.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
    )


def _parse_case_json(raw: str) -> dict:
    return json.loads(_strip_json_fences(raw))


def _validate_case_data(data: dict) -> None:
    suspects = data.get("suspects") or {}
    if not suspects:
        raise ValueError("case has no suspects")

    killer_id = data.get("killer_id")
    if not killer_id or killer_id not in suspects:
        raise ValueError(f"killer_id {killer_id!r} not in suspects")

    for sid, s in suspects.items():
        facts = s.get("facts") or []
        if not facts or not all(isinstance(f, str) and f.strip() for f in facts):
            raise ValueError(f"suspect {sid!r} has empty or invalid facts")
        is_killer = bool(s.get("is_killer"))
        if sid == killer_id and not is_killer:
            raise ValueError(f"killer_id {killer_id!r} has is_killer=false")
        if sid != killer_id and is_killer:
            raise ValueError(f"suspect {sid!r} marked is_killer but is not killer_id")

    clues = data.get("clues") or []
    if not clues:
        raise ValueError("case has no clues")
    for clue in clues:
        cond = clue.get("reveal_condition") or {}
        ctype = cond.get("type")
        if ctype not in _ALLOWED_REVEAL_TYPES:
            raise ValueError(
                f"clue {clue.get('id')!r} has unsupported reveal_condition.type {ctype!r}"
            )
        if cond.get("value") is None or str(cond.get("value")).strip() == "":
            raise ValueError(f"clue {clue.get('id')!r} has empty reveal_condition.value")


def _case_from_data(data: dict) -> Case:
    suspects: dict[str, Suspect] = {
        sid: Suspect(**s) for sid, s in data["suspects"].items()
    }
    clues: list[Clue] = [Clue(**c) for c in data["clues"]]
    return Case(
        victim=data["victim"],
        setting=data["setting"],
        killer_id=data["killer_id"],
        motive=data["motive"],
        solution=data["solution"],
        suspects=suspects,
        clues=clues,
    )


def _llm_case_json(theme: str | None, num_suspects: int) -> dict:
    """Call the GM LLM and parse JSON, retrying the call once on parse failure."""
    llm = get_gm_llm()
    prompt = _CASE_GENERATION_PROMPT.format(
        theme=theme or "a classic country manor", num_suspects=num_suspects
    )
    last_parse_error: Exception | None = None
    for _ in range(2):
        response = llm.invoke(prompt)
        raw = _content_text(response)
        try:
            return _parse_case_json(raw)
        except json.JSONDecodeError as e:
            last_parse_error = e
            continue
    raise ValueError(f"GM returned invalid JSON after retry: {last_parse_error}")


def generate_case(theme: str | None, num_suspects: int = 5) -> Case:
    """Generate a case; regenerate once if validation fails."""
    last_error: Exception | None = None
    for _ in range(2):
        try:
            data = _llm_case_json(theme, num_suspects)
            _validate_case_data(data)
            return _case_from_data(data)
        except ValueError as e:
            last_error = e
            continue
    raise ValueError(f"case generation failed after retry: {last_error}")


def new_game_id() -> str:
    return uuid.uuid4().hex[:10]
