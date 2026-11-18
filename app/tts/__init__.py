"""Azure TTS helpers for courtroom NPC speech.

Audio bytes never travel over the WebSocket; this package builds SSML and
talks to Azure. Caching, rate limits, and broadcasting live in layers above.
"""

from app.tts.azure import AzureSpeechClient
from app.tts.emotion import Emotion
from app.tts.ssml import Segment, build_ssml, split_first_sentence
from app.tts.voices import VoiceProfile, profile_for

__all__ = [
    "Emotion",
    "Segment",
    "VoiceProfile",
    "profile_for",
    "build_ssml",
    "split_first_sentence",
    "AzureSpeechClient",
]
