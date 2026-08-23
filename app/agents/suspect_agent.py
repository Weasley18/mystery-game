"""
Generates a single suspect's reply to a player's question.

The interesting problem here isn't the API call -- it's the prompt. Two
suspects get almost the same prompt template; the killer's gets one extra
block. Innocent suspects are told to answer truthfully from their facts.
The killer is told to never volunteer their real motive/actions, to
deflect specific questions, and -- critically -- to stay consistent with
whatever it has already told this player (that's why we pass the
per-suspect conversation history, not just the latest question).

History sent to the LLM is capped to the last 10 turns (20 messages) so
long sessions don't blow context or cost.
"""

from app.llm import invoke_suspect_llm
from app.models import Suspect

_HISTORY_TURN_CAP = 10  # turns = user+assistant pairs → 20 messages

_BASE_SYSTEM = """\
You are {name}, a suspect in a murder investigation. Speak in character.
Persona: {persona}
Backstory: {backstory}
Known facts you may reveal if directly relevant (never contradict these): {facts}

Stay in character. Keep answers to 2-4 sentences -- this is a live chat,
not a monologue. Never break character or mention you are an AI.
"""

_KILLER_ADDENDUM = """\
You are secretly the killer. Do not admit this under any circumstances.
Never volunteer your real motive or your actions the night of the murder.
If asked directly about the murder, deflect naturally in character or give
a partial truth that doesn't implicate you. Track what you've already told
this player in the conversation so far -- do not contradict an earlier lie.
"""


def suspect_reply(suspect: Suspect, conversation: list[dict], player_question: str) -> str:
    system = _BASE_SYSTEM.format(
        name=suspect["name"],
        persona=suspect["persona"],
        backstory=suspect["backstory"],
        facts="; ".join(suspect["facts"]),
    )
    if suspect["is_killer"]:
        system += "\n" + _KILLER_ADDENDUM

    # Cap to last N turns (user+assistant pairs)
    capped = conversation[-(_HISTORY_TURN_CAP * 2):]

    messages = [{"role": "system", "content": system}]
    messages.extend(capped)
    messages.append({"role": "user", "content": player_question})

    return invoke_suspect_llm(suspect["is_killer"], messages)
