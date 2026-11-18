#!/usr/bin/env python3
"""Audition courtroom voices without running the game.

This is the tuning rig for STYLE_MAP. Generate one line, or the whole
emotion x intensity matrix, listen, edit `app/tts/emotion.py`, repeat.

    # one line
    python scripts/tts_preview.py --role prosecution --emotion indignant \\
        --intensity 3 --text "You expect this court to believe that?"

    # every emotion for one role, at intensity 2
    python scripts/tts_preview.py --role prosecution --matrix

    # every emotion at all three intensities (30 requests; paced for F0)
    python scripts/tts_preview.py --role defense --matrix --intensities 1 2 3

    # see the SSML without spending a transaction
    python scripts/tts_preview.py --emotion grave --dry-run

    # which styles does a voice actually support?
    python scripts/tts_preview.py --list-styles en-US-DavisNeural

    # a scripted multi-emotion speech from JSON
    python scripts/tts_preview.py --role defense --json sample_speech.json

Requires AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in the environment or .env.
Output lands in ./tts_preview/ as MP3s named <role>_<voice>_<emotion>_i<n>.mp3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Make `app` importable when run as `python scripts/tts_preview.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tts.azure import (  # noqa: E402
    AzureRateLimited,
    AzureSpeechClient,
    AzureTTSConfigError,
    AzureTTSError,
    OUTPUT_FORMATS,
)
from app.tts.emotion import Emotion, STYLE_MAP, resolve  # noqa: E402
from app.tts.ssml import (  # noqa: E402
    Segment,
    build_ssml,
    chosen_style,
    plain_text,
    split_first_sentence,
)
from app.tts.voices import VOICE_PROFILES, profile_for  # noqa: E402

OUT_DIR = Path("tts_preview")

# F0 allows 20 transactions per 60 seconds. 3.2s between requests keeps a long
# --matrix run comfortably inside it instead of eating 429s halfway through.
F0_SPACING_SECONDS = 3.2

# Lines chosen to give each emotion something it can actually act.
DEFAULT_LINES: dict[Emotion, str] = {
    Emotion.MEASURED: "The prosecution will establish three facts, and only three.",
    Emotion.CONFIDENT: "Every piece of evidence points in exactly one direction.",
    Emotion.INDIGNANT: "The defendant had motive, means, and opportunity, and this court has been asked to ignore all three.",
    Emotion.SCORNFUL: "A coincidence, counsel calls it. How very convenient.",
    Emotion.GRAVE: "A woman is dead. Nothing we decide here will change that.",
    Emotion.URGENT: "Look at the timeline. Look at it now, before you vote.",
    Emotion.SYMPATHETIC: "No one doubts that you are frightened. Just tell the court what you saw.",
    Emotion.HUSHED: "There is one detail the prosecution would prefer you did not notice.",
    Emotion.TRIUMPHANT: "And there it is, members of the jury. The contradiction, in their own witness's words.",
    Emotion.RATTLED: "That is not — that is not what I said, and you know it.",
}


def _load_dotenv() -> None:
    """Minimal .env reader so the script works without python-dotenv."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _client(args: argparse.Namespace) -> AzureSpeechClient:
    return AzureSpeechClient(
        key=os.environ.get("AZURE_SPEECH_KEY", ""),
        region=os.environ.get("AZURE_SPEECH_REGION", ""),
        output_format=args.format,
    )


def _segments(args: argparse.Namespace, emotion: Emotion, intensity: int) -> list[Segment]:
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        raw = data.get("segments", data) if isinstance(data, dict) else data
        return [Segment.from_dict(item) for item in raw]
    text = args.text or DEFAULT_LINES[emotion]
    return [Segment(text=text, emotion=emotion, intensity=intensity)]


async def list_styles(args: argparse.Namespace) -> int:
    client = _client(args)
    try:
        voices = await client.voices()
    finally:
        await client.aclose()

    target = args.list_styles
    if target.lower() in ("all", "cast"):
        wanted = {p.voice for p in VOICE_PROFILES.values()}
    else:
        wanted = {target}

    for name in sorted(wanted):
        info = voices.get(name)
        if not info:
            print(f"{name}: NOT AVAILABLE in this region")
            continue
        styles = ", ".join(sorted(info.styles)) or "(none — prosody only)"
        print(f"\n{name}  [{info.locale}, {info.gender}]")
        print(f"  styles: {styles}")
        if info.roles:
            print(f"  roles:  {', '.join(sorted(info.roles))}")

        # Flag which of our emotions would silently lose their style tag.
        missing = [
            emo.value
            for emo, delivery in STYLE_MAP.items()
            if delivery.styles and not (set(delivery.styles) & info.styles)
        ]
        if missing:
            print(f"  ! prosody-only on this voice: {', '.join(missing)}")
    return 0


async def render(args: argparse.Namespace) -> int:
    profile = profile_for(args.role)
    voice = args.voice or profile.voice
    if args.voice:
        profile = type(profile)(
            voice=args.voice,
            locale=profile.locale,
            rate_bias_pct=profile.rate_bias_pct,
            pitch_bias_pct=profile.pitch_bias_pct,
            sentence_silence_ms=profile.sentence_silence_ms,
            style_overrides=profile.style_overrides,
        )

    emotions = list(Emotion) if args.matrix else [Emotion.coerce(args.emotion)]
    intensities = args.intensities if args.matrix else [args.intensity]

    client = None if args.dry_run else _client(args)
    supported = None
    if client is not None and not args.no_style_check:
        supported = await client.supported_styles(voice)
        if supported is None:
            print(f"! {voice} not found in region voice list; proceeding blind\n")

    OUT_DIR.mkdir(exist_ok=True)
    total = len(emotions) * len(intensities)
    done = 0
    failures = 0

    try:
        for emotion in emotions:
            for intensity in intensities:
                done += 1
                segments = _segments(args, emotion, intensity)
                delivery = resolve(
                    emotion,
                    intensity,
                    rate_bias_pct=profile.rate_bias_pct,
                    pitch_bias_pct=profile.pitch_bias_pct,
                )

                if args.two_chunk:
                    head, tail = split_first_sentence(segments)
                    plans = [("c0", head)] + ([("c1", tail)] if tail else [])
                else:
                    plans = [("", segments)]

                for suffix, chunk in plans:
                    try:
                        ssml = build_ssml(
                            chunk, profile, supported_styles=supported
                        )
                    except ValueError as exc:
                        print(f"[{done}/{total}] {emotion.value}: {exc}")
                        failures += 1
                        continue

                    label = f"{emotion.value} i{intensity}"
                    chars = len(plain_text(chunk))
                    style = chosen_style(profile, emotion, delivery, supported)
                    print(
                        f"[{done}/{total}] {label}{'/' + suffix if suffix else ''}  "
                        f"style={style or 'prosody-only'} "
                        f"degree={delivery.degree:.2f} "
                        f"rate={delivery.rate_pct:+d}% pitch={delivery.pitch_pct:+d}% "
                        f"chars={chars}"
                    )
                    if args.show_ssml or args.dry_run:
                        print(f"    {ssml}")
                    if client is None:
                        continue

                    try:
                        audio = await client.synthesize(ssml)
                    except AzureRateLimited as exc:
                        print(f"    rate limited; waiting {exc.retry_after or 20}s")
                        await asyncio.sleep(exc.retry_after or 20)
                        failures += 1
                        continue
                    except AzureTTSError as exc:
                        print(f"    FAILED: {exc}")
                        failures += 1
                        continue

                    stem = f"{args.role}_{voice}_{emotion.value}_i{intensity}"
                    if suffix:
                        stem = f"{stem}_{suffix}"
                    path = OUT_DIR / f"{stem}{client.file_extension}"
                    path.write_bytes(audio)
                    print(f"    -> {path}  ({len(audio) / 1024:.1f} KB)")

                    if done < total or len(plans) > 1:
                        await asyncio.sleep(F0_SPACING_SECONDS)
    finally:
        if client is not None:
            await client.aclose()

    if not args.dry_run:
        print(f"\n{total - failures}/{total} rendered into {OUT_DIR}/")
        if failures:
            print(f"{failures} failed — see messages above")
    return 1 if failures else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audition Azure TTS emotions for the courtroom cast.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--role", default="prosecution", choices=sorted(VOICE_PROFILES))
    p.add_argument("--voice", help="override the role's voice, e.g. en-US-GuyNeural")
    p.add_argument(
        "--emotion",
        default=Emotion.MEASURED.value,
        choices=[e.value for e in Emotion],
    )
    p.add_argument("--intensity", type=int, default=2, choices=(1, 2, 3))
    p.add_argument("--text", help="line to speak; defaults to a per-emotion sample")
    p.add_argument("--json", help="JSON file of segments for a multi-emotion speech")
    p.add_argument("--matrix", action="store_true", help="render every emotion")
    p.add_argument(
        "--intensities",
        type=int,
        nargs="+",
        default=[2],
        choices=(1, 2, 3),
        help="intensities to use with --matrix",
    )
    p.add_argument(
        "--two-chunk",
        action="store_true",
        help="split into fast-start + remainder, as the game does",
    )
    p.add_argument("--dry-run", action="store_true", help="print SSML, call nothing")
    p.add_argument("--show-ssml", action="store_true")
    p.add_argument(
        "--no-style-check",
        action="store_true",
        help="skip the voices/list lookup and send the preferred style blind",
    )
    p.add_argument("--format", default="mp3-24k-48k", choices=sorted(OUTPUT_FORMATS))
    p.add_argument(
        "--list-styles",
        metavar="VOICE",
        help="print a voice's supported styles ('cast' for the whole cast) and exit",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    _load_dotenv()
    try:
        if args.list_styles:
            return asyncio.run(list_styles(args))
        return asyncio.run(render(args))
    except AzureTTSConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    start = time.monotonic()
    code = main()
    print(f"done in {time.monotonic() - start:.1f}s")
    raise SystemExit(code)
