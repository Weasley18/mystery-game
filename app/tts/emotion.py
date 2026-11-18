"""Emotion taxonomy for courtroom speech, and its mapping to Azure delivery knobs.

This module is deliberately provider-agnostic in spirit: `Emotion` is what the
counsel agents emit, `Delivery` is the neutral bundle of prosody knobs, and only
the *contents* of STYLE_MAP are Azure-flavoured (the style names are Azure's
`mstts:express-as` styles). Swapping in another engine means rewriting
STYLE_MAP, not touching the agents.

Each emotion maps to an ordered tuple of candidate style names. The SSML builder
picks the first one the chosen voice actually supports, because Azure styles are
per-voice, not universal. An empty tuple means "prosody only, no express-as".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

__all__ = [
    "Emotion",
    "Delivery",
    "STYLE_MAP",
    "INTENSITY_SCALE",
    "resolve",
    "RATE_LIMITS",
    "PITCH_LIMITS",
    "DEGREE_LIMITS",
]


class Emotion(str, Enum):
    """The full set of emotions counsel agents may emit.

    Keep this small. Ten labels is enough range for a courtroom and keeps the
    LLM consistent; thirty labels makes it drift and multiplies the mapping.
    """

    MEASURED = "measured"
    CONFIDENT = "confident"
    INDIGNANT = "indignant"
    SCORNFUL = "scornful"
    GRAVE = "grave"
    URGENT = "urgent"
    SYMPATHETIC = "sympathetic"
    HUSHED = "hushed"
    TRIUMPHANT = "triumphant"
    RATTLED = "rattled"

    @classmethod
    def coerce(cls, value: object, default: "Emotion | None" = None) -> "Emotion":
        """Never raise on model output. Unknown labels fall back to `default`.

        The agents are told to use these exact strings, but a malformed emotion
        must never break a speech — losing expressiveness on one segment is
        fine, losing the segment is not.
        """
        fallback = default or cls.MEASURED
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            key = value.strip().lower().replace(" ", "_").replace("-", "_")
            try:
                return cls(key)
            except ValueError:
                return _ALIASES.get(key, fallback)
        return fallback


# Common synonyms an LLM reaches for despite instructions. Cheap insurance.
_ALIASES: dict[str, Emotion] = {
    "neutral": Emotion.MEASURED,
    "calm": Emotion.MEASURED,
    "matter_of_fact": Emotion.MEASURED,
    "assertive": Emotion.CONFIDENT,
    "firm": Emotion.CONFIDENT,
    "angry": Emotion.INDIGNANT,
    "outraged": Emotion.INDIGNANT,
    "incredulous": Emotion.SCORNFUL,
    "sarcastic": Emotion.SCORNFUL,
    "dismissive": Emotion.SCORNFUL,
    "contemptuous": Emotion.SCORNFUL,
    "somber": Emotion.GRAVE,
    "solemn": Emotion.GRAVE,
    "sad": Emotion.GRAVE,
    "alarmed": Emotion.URGENT,
    "insistent": Emotion.URGENT,
    "pleading": Emotion.SYMPATHETIC,
    "gentle": Emotion.SYMPATHETIC,
    "compassionate": Emotion.SYMPATHETIC,
    "empathetic": Emotion.SYMPATHETIC,
    "conspiratorial": Emotion.HUSHED,
    "whispering": Emotion.HUSHED,
    "quiet": Emotion.HUSHED,
    "vindicated": Emotion.TRIUMPHANT,
    "satisfied": Emotion.TRIUMPHANT,
    "flustered": Emotion.RATTLED,
    "defensive": Emotion.RATTLED,
    "nervous": Emotion.RATTLED,
    "afraid": Emotion.RATTLED,
}


@dataclass(frozen=True)
class Delivery:
    """Resolved prosody settings for one segment of speech.

    `styles` is a preference list; the SSML builder picks the first entry the
    voice supports. `rate_pct` / `pitch_pct` are *relative* percentages and are
    emitted as `<prosody rate="+10%" pitch="+4%">`.

    Note there is deliberately no volume knob. Azure's `prosody volume` accepts
    a different value grammar than the SSML spec suggests, and getting it wrong
    is a 400. Style plus rate plus pitch covers the useful range; if you want
    loudness differences, do them in the audio post-processing chain instead.
    """

    styles: tuple[str, ...]
    degree: float
    rate_pct: int
    pitch_pct: int
    break_after_ms: int = 0

    def scaled(self, factor: float) -> "Delivery":
        """Scale intensity-sensitive knobs by `factor`, clamped to safe ranges."""
        return replace(
            self,
            degree=_clamp(self.degree * factor, *DEGREE_LIMITS),
            rate_pct=int(round(_clamp(self.rate_pct * factor, *RATE_LIMITS))),
            pitch_pct=int(round(_clamp(self.pitch_pct * factor, *PITCH_LIMITS))),
        )

    def biased(self, rate_pct: int = 0, pitch_pct: int = 0) -> "Delivery":
        """Apply a per-voice bias (e.g. the judge speaks slower across the board)."""
        if not rate_pct and not pitch_pct:
            return self
        return replace(
            self,
            rate_pct=int(round(_clamp(self.rate_pct + rate_pct, *RATE_LIMITS))),
            pitch_pct=int(round(_clamp(self.pitch_pct + pitch_pct, *PITCH_LIMITS))),
        )


# Azure tolerates a wide range but quality degrades fast at the extremes, and
# `styledegree` above 2.0 is rejected outright.
RATE_LIMITS: tuple[float, float] = (-40.0, 60.0)
PITCH_LIMITS: tuple[float, float] = (-30.0, 30.0)
DEGREE_LIMITS: tuple[float, float] = (0.01, 2.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# The mapping. These numbers are a starting point, not gospel — run
# scripts/tts_preview.py --matrix and tune them by ear for your cast.
# --------------------------------------------------------------------------- #

STYLE_MAP: dict[Emotion, Delivery] = {
    # Baseline courtroom register. No style tag at all: the plain neural voice
    # is already a decent "professional speaking" default, and skipping
    # express-as here means MEASURED works on every voice ever shipped.
    Emotion.MEASURED: Delivery(
        styles=(),
        degree=1.0,
        rate_pct=0,
        pitch_pct=0,
        break_after_ms=180,
    ),
    Emotion.CONFIDENT: Delivery(
        styles=("excited", "friendly", "chat"),
        degree=0.9,
        rate_pct=4,
        pitch_pct=2,
        break_after_ms=180,
    ),
    Emotion.INDIGNANT: Delivery(
        styles=("angry",),
        degree=1.4,
        rate_pct=10,
        pitch_pct=4,
        break_after_ms=320,
    ),
    Emotion.SCORNFUL: Delivery(
        styles=("unfriendly", "angry"),
        degree=1.3,
        rate_pct=2,
        pitch_pct=-2,
        break_after_ms=260,
    ),
    Emotion.GRAVE: Delivery(
        styles=("sad",),
        degree=0.8,
        rate_pct=-8,
        pitch_pct=-3,
        break_after_ms=480,
    ),
    Emotion.URGENT: Delivery(
        styles=("shouting", "excited", "angry"),
        degree=0.8,
        rate_pct=12,
        pitch_pct=5,
        break_after_ms=200,
    ),
    Emotion.SYMPATHETIC: Delivery(
        styles=("empathetic", "hopeful", "friendly"),
        degree=1.2,
        rate_pct=-4,
        pitch_pct=0,
        break_after_ms=360,
    ),
    Emotion.HUSHED: Delivery(
        styles=("whispering",),
        degree=1.0,
        rate_pct=-6,
        pitch_pct=-2,
        break_after_ms=400,
    ),
    Emotion.TRIUMPHANT: Delivery(
        styles=("cheerful", "excited", "hopeful"),
        degree=1.25,
        rate_pct=2,
        pitch_pct=3,
        break_after_ms=300,
    ),
    Emotion.RATTLED: Delivery(
        styles=("terrified", "sad"),
        degree=0.7,
        rate_pct=6,
        pitch_pct=4,
        break_after_ms=240,
    ),
}


INTENSITY_SCALE: dict[int, float] = {1: 0.7, 2: 1.0, 3: 1.35}


def resolve(
    emotion: Emotion | str,
    intensity: int = 2,
    *,
    rate_bias_pct: int = 0,
    pitch_bias_pct: int = 0,
) -> Delivery:
    """Turn an (emotion, intensity) pair into concrete, clamped prosody knobs.

    `rate_bias_pct` / `pitch_bias_pct` come from the voice profile and apply
    after intensity scaling, so a slow-speaking judge stays slower than a fast
    prosecutor even at intensity 3.
    """
    emo = Emotion.coerce(emotion)
    factor = INTENSITY_SCALE.get(int(intensity) if str(intensity).isdigit() else 2, 1.0)
    return STYLE_MAP[emo].scaled(factor).biased(rate_bias_pct, pitch_bias_pct)
