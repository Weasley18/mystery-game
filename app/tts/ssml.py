"""Segment list -> Azure SSML.

Three jobs:

* Build one `<voice>` element containing a stack of `<mstts:express-as>` blocks,
  so a whole multi-emotion speech fits in a *single* Azure request. That matters
  because the F0 tier allows 20 transactions per 60 seconds and the limit is not
  adjustable — per-sentence requests would exhaust it in two speeches.
* Degrade gracefully. Styles are per-voice, so the builder picks the first
  candidate style the voice actually supports and drops `express-as` entirely if
  none of them are, keeping prosody. Passing `supported_styles=None` means
  "capability unknown, be optimistic".
* Split the first sentence off, so chunk 0 can start playing while chunk 1
  (the rest of the speech) is still synthesizing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence
from xml.sax.saxutils import escape, quoteattr

from .emotion import Delivery, Emotion, resolve
from .voices import VoiceProfile

__all__ = [
    "Segment",
    "SSMLTooLongError",
    "build_ssml",
    "split_first_sentence",
    "split_sentences",
    "plain_text",
    "total_chars",
    "MAX_TEXT_CHARS",
]

# Azure caps a real-time request at 10 minutes of audio. 6000 characters is
# roughly 7 minutes of speech, which leaves headroom and is far above anything
# a single counsel turn should produce.
MAX_TEXT_CHARS = 6000

# Control characters that are legal in a Python string but illegal in XML 1.0.
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Literal markup the model may echo (SSML/HTML). Escaping alone would make
# Azure speak the tags aloud; strip them before escape.
_TAG = re.compile(r"</?[a-zA-Z:][^>]*>")


class SSMLTooLongError(ValueError):
    """Raised when a single request would exceed Azure's per-request budget."""


@dataclass
class Segment:
    """One stretch of speech with a single emotional colour.

    This is the unit the counsel agents emit. `lead_pause_ms` is a beat *before*
    the segment — useful for the pause a lawyer takes before a reveal.
    """

    text: str
    emotion: Emotion = Emotion.MEASURED
    intensity: int = 2
    lead_pause_ms: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "Segment":
        """Tolerant constructor for LLM output. Never raises on bad fields."""
        try:
            intensity = int(data.get("intensity", 2))
        except (TypeError, ValueError):
            intensity = 2
        try:
            lead = int(data.get("lead_pause_ms", 0))
        except (TypeError, ValueError):
            lead = 0
        return cls(
            text=str(data.get("text", "")).strip(),
            emotion=Emotion.coerce(data.get("emotion")),
            intensity=max(1, min(3, intensity)),
            lead_pause_ms=max(0, min(2000, lead)),
        )


@dataclass
class _Block:
    """Internal: a run of segments that share identical resolved delivery."""

    delivery: Delivery
    style: str | None
    texts: list[str] = field(default_factory=list)
    lead_pause_ms: int = 0


def sanitize(text: str) -> str:
    """Strip illegal XML controls, literal markup tags, and collapse whitespace."""
    cleaned = _ILLEGAL_XML.sub("", text)
    cleaned = _TAG.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def plain_text(segments: Sequence[Segment]) -> str:
    """The spoken text with no markup — what you show as captions, and what the
    cache key and the character budget are computed from."""
    return " ".join(sanitize(s.text) for s in segments if sanitize(s.text))


def total_chars(segments: Sequence[Segment]) -> int:
    return len(plain_text(segments))


def _pick_style(
    candidates: Iterable[str],
    supported: frozenset[str] | set[str] | None,
) -> str | None:
    """First candidate the voice supports. `None` supported set = be optimistic."""
    candidates = tuple(candidates)
    if not candidates:
        return None
    if supported is None:
        return candidates[0]
    for style in candidates:
        if style in supported:
            return style
    return None


def chosen_style(
    profile: VoiceProfile,
    emotion: Emotion | str,
    delivery: Delivery,
    supported: frozenset[str] | set[str] | None = None,
) -> str | None:
    """The style `build_ssml` would actually emit, or None for prosody-only.

    Public so the preview CLI and the service layer can report and log the real
    choice rather than guessing at STYLE_MAP's first candidate — per-voice
    `style_overrides` mean those two often differ.
    """
    emo = Emotion.coerce(emotion)
    return _pick_style(profile.styles_for(emo, delivery.styles), supported)


def build_ssml(
    segments: Sequence[Segment],
    profile: VoiceProfile,
    *,
    supported_styles: frozenset[str] | set[str] | None = None,
    sentence_silence_ms: int | None = None,
    leading_silence_ms: int = 0,
) -> str:
    """Render segments as a single SSML document for `profile.voice`.

    `supported_styles` is the voice's `StyleList` from the Azure voices
    endpoint. Pass it whenever you have it; without it the builder assumes the
    preferred style works and you may get a 400 on an unsupported style.
    """
    usable = [s for s in segments if sanitize(s.text)]
    if not usable:
        raise ValueError("build_ssml: no segments with speakable text")

    text_len = total_chars(usable)
    if text_len > MAX_TEXT_CHARS:
        raise SSMLTooLongError(
            f"{text_len} characters exceeds the {MAX_TEXT_CHARS}-character "
            "per-request limit; split the utterance into more chunks"
        )

    # Coalesce neighbouring segments with identical delivery so we emit one
    # express-as block per emotional beat rather than one per sentence.
    blocks: list[_Block] = []
    for seg in usable:
        delivery = resolve(
            seg.emotion,
            seg.intensity,
            rate_bias_pct=profile.rate_bias_pct,
            pitch_bias_pct=profile.pitch_bias_pct,
        )
        style = chosen_style(profile, seg.emotion, delivery, supported_styles)
        mergeable = (
            blocks
            and not seg.lead_pause_ms
            and blocks[-1].delivery == delivery
            and blocks[-1].style == style
        )
        if mergeable:
            blocks[-1].texts.append(sanitize(seg.text))
        else:
            blocks.append(
                _Block(
                    delivery=delivery,
                    style=style,
                    texts=[sanitize(seg.text)],
                    lead_pause_ms=seg.lead_pause_ms,
                )
            )

    silence = profile.sentence_silence_ms if sentence_silence_ms is None else sentence_silence_ms

    parts: list[str] = [
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" '
        f"xml:lang={quoteattr(profile.locale)}>",
        f"<voice name={quoteattr(profile.voice)}>",
    ]
    if silence > 0:
        parts.append(
            f'<mstts:silence type="Sentenceboundary" value="{int(silence)}ms"/>'
        )
    if leading_silence_ms > 0:
        parts.append(f'<break time="{int(leading_silence_ms)}ms"/>')

    # A block's trailing beat and the next block's lead pause describe the same
    # silence, so hold the beat and emit one `<break>` of the larger value
    # rather than two stacked ones.
    pending_break_ms = 0

    for index, block in enumerate(blocks):
        pause = max(pending_break_ms, block.lead_pause_ms)
        if pause > 0:
            parts.append(f'<break time="{int(pause)}ms"/>')
        pending_break_ms = 0

        body = escape(" ".join(t for t in block.texts if t))
        d = block.delivery
        if d.rate_pct or d.pitch_pct:
            body = (
                f'<prosody rate="{d.rate_pct:+d}%" pitch="{d.pitch_pct:+d}%">'
                f"{body}</prosody>"
            )
        if block.style:
            body = (
                f'<mstts:express-as style={quoteattr(block.style)} '
                f'styledegree="{d.degree:.2f}">{body}</mstts:express-as>'
            )
        parts.append(body)

        # A beat between emotional shifts, but never a trailing pause on the
        # last block — that just adds dead air before the next speaker.
        if index < len(blocks) - 1:
            pending_break_ms = d.break_after_ms

    parts.append("</voice></speak>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Sentence splitting, for the two-chunk strategy
# --------------------------------------------------------------------------- #

# Tokens that end in a period without ending a sentence. Not exhaustive, but it
# covers the ones that actually show up in legal argument.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "no", "vs", "v",
    "inc", "ltd", "co", "approx", "est", "etc", "e.g", "i.e", "al", "fig",
    "hon", "capt", "sgt", "lt", "det", "pp", "am", "pm", "a.m", "p.m",
}

_SENTENCE_END = re.compile(r'(?<=[.!?\u2026])["\'\u2019\u201d)\]]*\s+')


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, skipping known abbreviations."""
    text = sanitize(text)
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        candidate = text[start : match.start()].strip()
        if not candidate:
            continue
        # "Dr." / "Mr." — the period belongs to the token, not the sentence.
        last_word = re.split(r"[\s(]", candidate)[-1].rstrip('"\'\u2019\u201d)]')
        if last_word.lower().rstrip(".") in _ABBREVIATIONS:
            continue
        # A single capital followed by a period is almost always an initial.
        if re.fullmatch(r"[A-Z]\.", last_word):
            continue
        sentences.append(candidate)
        start = match.end()

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_first_sentence(
    segments: Sequence[Segment],
) -> tuple[list[Segment], list[Segment]]:
    """Peel the opening sentence off, for the fast-start chunk.

    Returns `(head, tail)`. `tail` may be empty for a one-sentence utterance, in
    which case you send a single request and skip the second entirely.
    """
    usable = [s for s in segments if sanitize(s.text)]
    if not usable:
        return [], []

    first = usable[0]
    sentences = split_sentences(first.text)

    # Nothing to peel: the whole first segment is one sentence.
    if len(sentences) <= 1:
        return [first], list(usable[1:])

    head = Segment(
        text=sentences[0],
        emotion=first.emotion,
        intensity=first.intensity,
        lead_pause_ms=first.lead_pause_ms,
    )
    remainder = Segment(
        text=" ".join(sentences[1:]),
        emotion=first.emotion,
        intensity=first.intensity,
        lead_pause_ms=0,
    )
    return [head], [remainder, *usable[1:]]
