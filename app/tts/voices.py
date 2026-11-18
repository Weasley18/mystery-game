"""Casting: which Azure voice plays which courtroom role.

Two things live here:

1. `VoiceProfile` — a voice plus per-character biases, so the judge can be
   globally slower and lower without touching the emotion map.
2. `VOICE_PROFILES` — the default cast. Scenario JSON can override any of it
   via a `voices` block, which is why the profile is a plain dataclass with a
   `from_dict` classmethod.

The voices chosen below all ship a reasonably wide `StyleList`. If you swap in
a different voice, run `scripts/tts_preview.py --list-styles <voice>` first:
many of the newer multilingual voices sound great but support no styles at all,
which silently costs you all your emotion range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .emotion import Emotion

__all__ = ["VoiceProfile", "VOICE_PROFILES", "profile_for", "DEFAULT_ROLE"]

DEFAULT_ROLE = "narrator"


@dataclass(frozen=True)
class VoiceProfile:
    """How one character sounds."""

    voice: str
    locale: str = "en-US"

    # Applied to every segment this character speaks, after intensity scaling.
    rate_bias_pct: int = 0
    pitch_bias_pct: int = 0

    # Azure's `mstts:silence type="Sentenceboundary"`. Raising this is the
    # single cheapest way to make a voice sound like it is addressing a room
    # rather than reading a paragraph.
    sentence_silence_ms: int = 250

    # Per-character style substitutions, e.g. give Aria "narration-professional"
    # for MEASURED while every other voice stays prosody-only.
    style_overrides: Mapping[Emotion, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoiceProfile":
        """Build from a scenario JSON `voices` entry, ignoring unknown keys."""
        overrides: dict[Emotion, tuple[str, ...]] = {}
        for key, value in (data.get("style_overrides") or {}).items():
            emo = Emotion.coerce(key)
            if isinstance(value, str):
                overrides[emo] = (value,)
            elif isinstance(value, (list, tuple)):
                overrides[emo] = tuple(str(v) for v in value)
        return cls(
            voice=str(data["voice"]),
            locale=str(data.get("locale", "en-US")),
            rate_bias_pct=int(data.get("rate_bias_pct", 0)),
            pitch_bias_pct=int(data.get("pitch_bias_pct", 0)),
            sentence_silence_ms=int(data.get("sentence_silence_ms", 250)),
            style_overrides=overrides,
        )

    def styles_for(self, emotion: Emotion, default: tuple[str, ...]) -> tuple[str, ...]:
        return self.style_overrides.get(emotion, default)


VOICE_PROFILES: dict[str, VoiceProfile] = {
    # Clipped, forward, a little aggressive. Davis carries `angry` and
    # `unfriendly` well, which are the prosecution's two workhorses.
    "prosecution": VoiceProfile(
        voice="en-US-DavisNeural",
        rate_bias_pct=2,
        sentence_silence_ms=220,
    ),
    # Aria has the widest style list of any en-US voice, including the
    # `empathetic` and `narration-professional` styles the defense leans on.
    "defense": VoiceProfile(
        voice="en-US-AriaNeural",
        rate_bias_pct=-2,
        sentence_silence_ms=280,
        style_overrides={Emotion.MEASURED: ("narration-professional",)},
    ),
    # Slow, low, unhurried. Newscast-formal reads as institutional authority.
    "judge": VoiceProfile(
        voice="en-US-TonyNeural",
        rate_bias_pct=-6,
        pitch_bias_pct=-2,
        sentence_silence_ms=400,
        style_overrides={
            Emotion.MEASURED: ("newscast-formal",),
            Emotion.GRAVE: ("newscast-formal", "sad"),
        },
    ),
    "witness": VoiceProfile(
        voice="en-US-SaraNeural",
        sentence_silence_ms=300,
    ),
    DEFAULT_ROLE: VoiceProfile(
        voice="en-US-AriaNeural",
        sentence_silence_ms=260,
        style_overrides={Emotion.MEASURED: ("narration-professional",)},
    ),
}


def profile_for(
    role: str | None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> VoiceProfile:
    """Resolve a role name to a profile, honouring a scenario's `voices` block.

    Falls back to the narrator profile rather than raising: an unrecognised
    speaker should still be audible.
    """
    key = (role or DEFAULT_ROLE).strip().lower()
    if overrides and key in overrides:
        try:
            return VoiceProfile.from_dict(overrides[key])
        except (KeyError, TypeError, ValueError):
            pass  # Malformed scenario data: fall through to the built-in cast.
    return VOICE_PROFILES.get(key, VOICE_PROFILES[DEFAULT_ROLE])
