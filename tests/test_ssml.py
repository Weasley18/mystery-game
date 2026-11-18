"""Offline SSML builder tests — no Azure network calls."""

from __future__ import annotations

from app.tts.emotion import Emotion
from app.tts.ssml import (
    Segment,
    build_ssml,
    sanitize,
    split_first_sentence,
)
from app.tts.voices import profile_for


def test_sanitize_strips_markup_tags():
    raw = 'He said </voice><voice name="evil">hello</voice> there.'
    assert sanitize(raw) == "He said hello there."


def test_sanitize_strips_illegal_xml_controls():
    assert sanitize("hello\x00world\x07") == "helloworld"


def test_split_first_sentence_keeps_abbreviation_intact():
    segments = [
        Segment(
            text="Mr. Ashcroft left at 9 p.m., they say. That is the whole case.",
            emotion=Emotion.MEASURED,
        )
    ]
    head, tail = split_first_sentence(segments)
    assert len(head) == 1
    assert head[0].text == "Mr. Ashcroft left at 9 p.m., they say."
    assert len(tail) == 1
    assert tail[0].text == "That is the whole case."


def test_coalesce_adjacent_identical_delivery():
    profile = profile_for("prosecution")
    segments = [
        Segment(text="First beat.", emotion=Emotion.INDIGNANT, intensity=2),
        Segment(text="Second beat.", emotion=Emotion.INDIGNANT, intensity=2),
    ]
    ssml = build_ssml(segments, profile, supported_styles=frozenset({"angry"}))
    # One express-as for both sentences, not two.
    assert ssml.count("<mstts:express-as") == 1
    assert "First beat. Second beat." in ssml


def test_measured_omits_express_as_without_override():
    profile = profile_for("prosecution")
    segments = [Segment(text="The facts are simple.", emotion=Emotion.MEASURED)]
    ssml = build_ssml(segments, profile, supported_styles=frozenset({"angry", "sad"}))
    assert "express-as" not in ssml
    assert "The facts are simple." in ssml


def test_express_as_present_when_style_supported():
    profile = profile_for("prosecution")
    segments = [
        Segment(text="This is outrageous.", emotion=Emotion.INDIGNANT, intensity=2)
    ]
    ssml = build_ssml(segments, profile, supported_styles=frozenset({"angry"}))
    assert 'style="angry"' in ssml
    assert "styledegree=" in ssml
    assert "This is outrageous." in ssml


def test_unsupported_style_degrades_to_prosody_only():
    profile = profile_for("prosecution")
    segments = [
        Segment(text="Quiet now.", emotion=Emotion.HUSHED, intensity=2)
    ]
    # whispering not in supported set → no express-as, but prosody remains.
    ssml = build_ssml(segments, profile, supported_styles=frozenset({"angry", "sad"}))
    assert "express-as" not in ssml
    assert 'rate="-6%"' in ssml or "rate=" in ssml
    assert "Quiet now." in ssml


def test_defense_measured_uses_narration_professional_override():
    profile = profile_for("defense")
    segments = [Segment(text="Members of the jury.", emotion=Emotion.MEASURED)]
    ssml = build_ssml(
        segments,
        profile,
        supported_styles=frozenset({"narration-professional"}),
    )
    assert 'style="narration-professional"' in ssml
