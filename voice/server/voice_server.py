#!/usr/bin/env python3
"""The voice brain on sunday. Everything runs on the Pi except Claude itself.

One websocket per cube. The cube sends 16 kHz mono s16le PCM as binary frames
and small JSON control messages. This side finds the end of the utterance with
Silero VAD, transcribes it with Moonshine, streams it through a long-lived
Claude Code session (the Pi's own login), and speaks each sentence with Piper as
soon as Claude finishes writing it.

Cube -> server:  {"type":"hello","device":...}
                 {"type":"wake"}            start listening; while it speaks, a barge-in that ducks
                                            rather than cuts it until barge.py has decided
                 <binary PCM>               mic audio, between wake and stop_listening, and between
                                            ear on and off
                 {"type":"ear","on":bool}   while it talks it is already awake: it streams its mic
                                            with no wake word, and what he says over it is decided
                                            here (stopped, corrected, or left to finish unanswered)
Server -> cube:  {"type":"stop_listening"}  the utterance is over, close the mic
                 {"type":"speak_start","rate":48000} <binary PCM ...> {"type":"speak_end"}
                 {"type":"sentence","text":..,"seq":n,"boot":..} before each clause's audio, and
                 {"type":"clause_end","seq":n} after each clause's audio, and
                 {"type":"sentence_end","seq":n} once the whole sentence n has been sent
                 {"type":"stop_audio"}      drop whatever is queued for the speaker
                 {"type":"duck","on":bool}  play quieter while he talks over it, or back to full
                 {"type":"listen"}          open the mic again without a wake word
                 {"type":"idle"}            nothing more is coming for this turn
                 {"type":"error","text":...}
Cube -> server:  {"type":"light","on":bool}  its ring lit (listening, thinking, speaking) or dark
                 "hello" may carry "resume":{"boot":..,"have":n}: after a dropped link, the last
                 sentence it holds whole. Everything after it is sent again, from that sentence on.
Server -> popup (/events): {"type":"light","on":bool} any cube's ring is lit; also in "hello".
                 {"type":"overlay","on":bool,"by":..} "overlay on" or "off", said to Sunday: the
                 Windows popup draws nothing while off, and holds back over a game unless on.
Popup -> server: {"type":"overlay","machine","shown","game","off","gameOn"} the Windows popup's
                 overlay as it stands, on connect and on every change; in /health as "overlay".
                 Every event about one question or announcement carries
                 its "id": listening, partial, status, heard, reply (spoken text only),
                 speaking, done, spoken (its audio has finished playing), and announce.
                 An "idle" with no id means nothing is in progress.
                 {"type":"task","state":"start"|"update"|"end","id","kind","label","status"}
                 one item of work in progress: a question, a relayed message, a new chat,
                 a transcript search. The current ones arrive with "hello" as "active".
                 {"type":"sync","active":[...],"live":[ids],"playing":[ids]} every SYNC_EVERY
                 seconds, and the same three lists in "hello": the queue as it really is, the
                 cards still in progress and the ones still being heard. The popup lets go of
                 anything not in them, so a lost "done" or "end" can never leave one stuck.
Local services -> server: GET /activity?state=start|end&id=..&kind=..&label=..&status=..&pid=..
                 An item belongs to the question whose Claude process started it and ends with
                 that question, however it ends; any item ends when its reporting process dies.
                 GET /late?about=..&text=..&asked=..  a lookup that answered after the question
                 it was for had already been answered: said on its own, briefly, if still worth it,
                 once he can hear it (the late answers, below the repeat guard).
                 GET /show?spec=<canvas JSON> | ?md=<markdown>&title=.. | ?close=1  a canvas on his
                 screen from any of his sessions; it answers with the screens it reached.
Server -> popup: {"type":"canvas","id","title","subtitle","question","said","source","blocks":[...]}
                 the detail of an answer, full screen (canvas.py has the blocks). Nothing in it is
                 spoken. {"type":"canvas_close","id","by"} takes it down on every screen.
                 "listening" and "speaking" carry "device" and "ear" (the Mac); "listening" also
                 "followup". "status" carries "kind": thinking, tool, filler or retry. An interrupted
                 "done" carries "cause", a command's "done" its "command". "hello" and "sync" carry
                 "devices": the cubes and ears connected and the cubes whose mute button is on.
Popup -> server: {"type":"canvas_closed","id","by"} he closed it there (Esc, a click, the button);
                 {"type":"canvas_state","id","machine","shown","why"} where it landed or was held back.
"""

import asyncio
import contextlib
import itertools
import json
import logging
import os
import re
import sqlite3
import time
from collections import deque
from pathlib import Path

import numpy as np
import sherpa_onnx
import soxr
import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.protocol import State
import websockets.http11

# A /show canvas arrives in its request line, which websockets caps at 8 KB by default.
websockets.http11.MAX_LINE_LENGTH = max(websockets.http11.MAX_LINE_LENGTH, 1 << 18)

import barge as bargein
import canvas as canvases
import nonspeech
import pronounce
import repeat_guard as repeats
import mtoa_config
import speaker_id
import speech_gate

_render = mtoa_config.render

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
DATA = Path(os.environ.get("VOICE_DATA", HERE / "data"))
DATA.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("VOICE_MODEL", "claude-sonnet-5-5")
NOTES_MODEL = os.environ.get("VOICE_NOTES_MODEL", "claude-haiku-4-5")
VOICE = os.environ.get("VOICE_TTS", "vits-piper-en_US-hfc_female-medium")
VOICE_SID = int(os.environ.get("VOICE_TTS_SPEAKER", "0"))
VOICE_SPEED = float(os.environ.get("VOICE_TTS_SPEED", "1.0"))
KOKORO = VOICE.startswith("kokoro")   # a sherpa-onnx Kokoro release; anything else is a Piper voice
TTS_THREADS = int(os.environ.get("VOICE_TTS_THREADS", "4"))
# Kokoro runs slower than real time on the Pi, so a long sentence is spoken a clause
# at a time: the first clause plays while the rest is still being made.
TTS_CLAUSES = os.environ.get("VOICE_TTS_CLAUSES", "1" if KOKORO else "0") == "1"
HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "8794"))
# Trailing silence that ends his turn. Long enough that a pause to think mid-sentence
# never cuts him off.
END_SILENCE = float(os.environ.get("VOICE_END_SILENCE", "1.8"))
VAD_THRESHOLD = float(os.environ.get("VOICE_VAD_THRESHOLD", "0.5"))
NO_SPEECH_RETRY_DB = float(os.environ.get("VOICE_NO_SPEECH_RETRY_DB", "-38"))   # sound this loud is transcribed even if the detector missed it
NO_SPEECH_TIMEOUT = float(os.environ.get("VOICE_NO_SPEECH_TIMEOUT", "6"))
PARTIAL_EVERY = float(os.environ.get("VOICE_PARTIAL_EVERY", "0.6"))
SPOKEN_WORD_CAP = int(os.environ.get("VOICE_SPOKEN_WORD_CAP", "60"))
ACK_MODEL = os.environ.get("VOICE_ACK_MODEL", "claude-haiku-4-5")
LATE_MODEL = os.environ.get("VOICE_LATE_MODEL", "claude-haiku-4-5")
FILLER_AFTER = float(os.environ.get("VOICE_FILLER_AFTER", "1.2"))
MIC_SETTLE = int(16000 * 0.2)   # skip the mic-open click
MAX_UTTERANCE = float(os.environ.get("VOICE_MAX_UTTERANCE", "45"))
SESSION_IDLE = float(os.environ.get("VOICE_SESSION_IDLE", "1800"))
FOLLOWUP = os.environ.get("VOICE_FOLLOWUP", "1") == "1"
FOLLOWUP_WINDOW = float(os.environ.get("VOICE_FOLLOWUP_WINDOW", "5.5"))   # seconds to start talking after a reply
# While a device talks it is already awake: it streams its mic with no wake word and
# what he says over it is heard here. Only an obvious stop stops it, only an explicit
# correction cuts it to answer, and words that start with its name are taken as a wake.
# Anything else is not for Sunday: it is never answered and it never interrupts.
OVER = os.environ.get("VOICE_OVER", "1") == "1"
OVER_END_SILENCE = float(os.environ.get("VOICE_OVER_END_SILENCE", "0.7"))   # a pause this long ends what he said
OVER_PAD = 0.3              # seconds kept before the detector's start, which it finds a little late
OVER_MAX = float(os.environ.get("VOICE_OVER_MAX", "20"))      # a stretch of speech longer than this is decided as it stands
OVER_KEEP = OVER_MAX + 5
OVER_WINDOW = (2.5, 0.5)    # what it was saying from this long before he started to this long after he stopped
# Its own voice keeps the detector open for most of what it says, so a stop is caught from a
# running transcript of the last moment rather than waiting for a pause. The window is short
# on purpose: given more of its own fluent speech around a short "stop", the recognizer folds
# the word into it ("store"), and with 1.2 s it finds 21 of 24 against 1.8 s's half. Not while
# Kokoro is making speech: the recognizer beside it slows Kokoro by half, which is a gap.
OVER_PARTIAL_EVERY = float(os.environ.get("VOICE_OVER_PARTIAL_EVERY", "0.6"))
OVER_TAIL = 1.2
OVER_AUDIO_GATE = "computer audio playing"
OVER_LOUD_HOLD = 2.5        # seconds a loud stretch keeps it turned down with nothing for Sunday in it
OVER_CUE_HOLD = 4.0         # and its name, while what follows is still being decided
# His voice over the cube's speech reaches the open ear at about -50 dBFS, under the level
# the detector reliably calls speech, so what it hears is turned up first (for the detector
# only). Its own residual voice then counts as speech too, which the running transcript sorts out.
OVER_VAD_GAIN = 10 ** (float(os.environ.get("VOICE_OVER_VAD_GAIN_DB", "18")) / 20)
# Speech this much louder than its own residual voice has been lately is him, close enough
# to turn it down at once, which lets the rest of what he says be heard 9 dB more clearly.
# If the running transcript finds nothing of his soon after, the volume comes straight back.
OVER_DUCK_DB = float(os.environ.get("VOICE_OVER_DUCK_DB", "8"))
# While one of his computers reports sound playing, its audio can wake the cube and be
# taken for him. Then: no follow-up window, and a transcript must name Sunday, or be at
# least GATE_MIN_WORDS words that a quick check judges said to the assistant.
AUDIO_GATE = os.environ.get("VOICE_AUDIO_GATE", "1") == "1"
GATE_MIN_WORDS = int(os.environ.get("VOICE_GATE_MIN_WORDS", "3"))
GATE_CLASSIFY = os.environ.get("VOICE_GATE_CLASSIFY", "1") == "1"
FILLER = set(os.environ.get("VOICE_FILLER_WORDS",
                            "so um uh uhm hmm hm mm mhm oh ah er huh like well and the a but").split())
# The Mac's ear defers to the cube. A wake it hears waits this long for the cube to
# report the same wake before the Mac is told to go ahead; a cube wake this soon after
# the Mac was cleared is the same word heard late, and the cube stands down. Measured
# with both hearing one "Sunday": the cube's report lands 6 to 82 ms after the Mac's.
EAR_WAIT = float(os.environ.get("VOICE_EAR_WAIT", "0.4"))
EAR_CUBE_BEFORE = float(os.environ.get("VOICE_EAR_CUBE_BEFORE", "1.5"))   # a cube wake this long before the Mac's counts
EAR_LATE_CUBE = float(os.environ.get("VOICE_EAR_LATE_CUBE", "1.5"))
# A Mac wake whose own second never held its name, and whose running transcripts have not
# turned it up since, was a television or a room talking: the mic closes this soon rather
# than staying open on that talk for a whole utterance, so the next "Sunday" finds it free.
UNNAMED_CLOSE_S = float(os.environ.get("VOICE_UNNAMED_CLOSE", "4.0"))
# A Mac listen that has heard its name and nothing after it, when he pauses: "Sunday." and a
# wait for the chime, which waits for his words, or its name said again. The mic stays open
# this long after the pause for what he wants, instead of the listen ending as its name and
# nothing after it while he is still to say it.
NAME_WAIT_S = float(os.environ.get("VOICE_NAME_WAIT", "8.0"))
# A Mac wake word said at the Mac and then a pause is him waiting for the chime, however the
# second before it transcribed: at least this loud, and this much quieter from LONE_FROM to
# LONE_TO after the mic opened. A television or the room goes on talking.
LONE_LOUD = float(os.environ.get("VOICE_LONE_LOUD", "-22"))
LONE_DROP = float(os.environ.get("VOICE_LONE_DROP", "12"))
LONE_FROM, LONE_TO = 0.45, 1.05
# While a home cube talks it is already awake: its open ear hears him over its speech, and
# its wake word stays live. A Mac wake then stands down, since the likeliest thing the Mac
# heard is the cube's own voice, which its voice processing cannot cancel. That holds through
# the cube's ear tail after playback (1 s on the cube), with a margin for its playback
# running behind the clock here.
CUBE_EAR_TAIL = float(os.environ.get("VOICE_CUBE_EAR_TAIL", "1.5"))
# A cube that reports when its speaker is heard in the room ("playing", from its loopback and
# the echo tail it measures) holds a Mac wake off only while its voice could have been the
# word: still audible, or audible until less than this long before the wake arrived. A wake
# word fires 0.02 to 0.38 s after the word ends (23 real wakes, 2026-10-06), so a word he
# starts once the cube has gone quiet arrives at least 0.62 s after it, and is let through.
EAR_FIRE_LAG = float(os.environ.get("VOICE_EAR_FIRE_LAG", "0.4"))
# The wake word itself must be heard before anything chimes, lights or shows: the last second
# before the wake fired, which holds all of "Sunday" (it starts 0.62 to 0.86 s before).
WORD_CHECK_S = 1.0
# A transcript is checked for Sunday's own voice coming back (barge.own_voice) against what
# any device was playing while he spoke, from this long before he started: the cube's
# playback runs behind the clock here by its jitter buffer, and its voice rings on briefly.
# Not further back: speech that had finished by then cannot be in what the mic heard, and
# his answer to an offer it made a while ago repeats its words ("I can shorten the wait to
# five days" - "Shorten the wait to five days"); against the last 20 s, two such real
# requests in the log of 2026-10-05/06 would have been dropped.
ECHO_LAG = float(os.environ.get("VOICE_ECHO_LAG", "5"))
# A device woken while it was quiet cannot hear what it had finished saying, beyond this tail
# of playback running behind the clock here. Without it, "Sunday, shorten the wait to five
# days" a moment after it offered just that would be dropped as its own voice.
OWN_TAIL = float(os.environ.get("VOICE_OWN_TAIL", "1.0"))
# Two devices in one room: speaking on one goes into the other's open mic and comes
# back transcribed as a question. An answer waits this long for another device to
# stop listening, then goes ahead rather than being swallowed by a stuck mic.
OTHER_MIC_WAIT = float(os.environ.get("VOICE_OTHER_MIC_WAIT", "10"))
# A late answer (a lookup's reply handed on by relay.py, or /say?answer=1) waits for a moment
# he can hear it: no mic open for him, nobody talking, his newer question answered, and then
# this much quiet. It plays on the device he last spoke to, if that was this recent.
LATE_QUIET = float(os.environ.get("VOICE_LATE_QUIET", "1.5"))
LATE_TURN_WAIT = float(os.environ.get("VOICE_LATE_TURN_WAIT", "180"))   # his newer question is waited on this long
LATE_HERE = float(os.environ.get("VOICE_LATE_HERE", "900"))
LATE_TRIES = int(os.environ.get("VOICE_LATE_TRIES", "3"))   # turned down or cut off this often, it is left unsaid
LATE_LISTEN_GRACE = 2.0     # a reply asks for a follow-up window, which opens once its playback ends
LATE_KEEP = 600             # a late answer is remembered this long, so a second copy of it is known
# Whose voice it is (speaker_id.py). Every wake, follow-up window and stretch the open ear
# hears is scored against his voiceprint and logged. In shadow mode nothing is blocked; once
# enforcing, what is plainly not him is ignored without a sound, a borderline score is settled
# by whether the words were said to Sunday, and an obvious stop or mute over its speech acts
# whoever said it. While enforcing, the devices hold a wake's chime, ring and overlay until the
# brain says it is him ({"type":"verify","hold":true}); the brain says so at once when the
# word itself scores as him, or once enough of what follows does.
SPEAKER = os.environ.get("VOICE_SPEAKER", "1") == "1"
SPEAKER_EARLY_S = float(os.environ.get("VOICE_SPEAKER_EARLY", "0.8"))   # heard after the wake, scored before the chime
SPEAKER_BARGE_SLACK = 0.1     # over its own ducked voice his score drops: reject only this far under the line
# Before anything chimes, lights, turns down or shows, the words that clear a listen must be
# his: a voice scoring at least this is his, and his words show at once (every television and
# room stretch scored 0.28 or less, 2026-10-05 to 10-08). Under it, the directed judge must
# read the words as said to Sunday, which takes 0.5 to 2 s: a short question is often over
# first, and then it is decided when he stops.
SPEAKER_HIS = float(os.environ.get("VOICE_SPEAKER_HIS", "0.35"))
# A wake nothing cleared is dropped when he stops only when both say it was not him: the judge
# reads the whole of it as overheard, and the voice scores under this. The judge alone called
# one in four of his requests overheard; each of those scored 0.38 or more.
SPEAKER_NOT_HIS = float(os.environ.get("VOICE_SPEAKER_NOT_HIS", "0.25"))
FOLLOWED_UP = float(os.environ.get("VOICE_SPEAKER_FOLLOWED_UP", "180"))  # a turn followed this soon is his
# The cube reports how much of what its mic heard at a wake its own speaker explains: its echo
# canceller's residual in the wake against the residual of the seconds before. A wake while it
# plays with no more residual than its own voice leaves, in a voice not plainly his, is its own
# voice. Never on the residual alone: his voice from across the room is no louder than that.
OWN_PLAYBACK_DB = float(os.environ.get("VOICE_OWN_PLAYBACK_DB", "3"))
PREROLL_S = 1.5
# Asked once (GET /enroll from this machine), when there is too little of his voice to go on.
ENROLL_ASK = ("To make sure I only answer you, and not the TV or your phone, I need to learn your voice. "
              "When I stop talking, tell me in a few sentences about your day.")
THREADS = int(os.environ.get("VOICE_THREADS", "3"))   # questions that can be worked on at once
RATE = 16000
GATE_RETRIES = int(os.environ.get("VOICE_GATE_RETRIES", "2"))   # a broken answer is sent back this many times
OUT_RATE = 48000             # the cube's speaker runs at 48 kHz; resample here, where CPU is cheap
# The cube's link. A ping every 5 s gives the cube something to hear while idle (it reconnects
# after 15 s of silence); 30 s without a pong is what it takes to give the link up, so a Wi-Fi
# hiccup of a few seconds never kills a reply. Speech sent while the link is down waits in the
# connection's outbox, and a cube that comes back within RESUME_WAIT is sent it again from the
# first sentence it did not get whole, in the audio already made for it.
PING_INTERVAL = float(os.environ.get("VOICE_PING_INTERVAL", "5"))
PING_TIMEOUT = float(os.environ.get("VOICE_PING_TIMEOUT", "30"))
RESUME_WAIT = float(os.environ.get("VOICE_RESUME_WAIT", "240"))   # today's drops ran 1.5 to 3.5 min
WRITE_LIMIT = 1 << 22        # about 40 s of 48 kHz audio before a send waits on the network
BOOT = str(int(time.time()))

log = logging.getLogger("voice")
started_at = time.time()
last_turn = {"at": None, "latency_ms": None, "heard": None}


# ---------------------------------------------------------------- local models

def load_asr():
    d = MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(d / "encoder.int8.onnx"),
        decoder=str(d / "decoder.int8.onnx"),
        joiner=str(d / "joiner.int8.onnx"),
        tokens=str(d / "tokens.txt"),
        model_type="nemo_transducer",
        num_threads=4,
    )


def load_tts():
    d = MODELS / VOICE
    onnx = next(d.glob("*.onnx"))
    if KOKORO:
        model = sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(onnx), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
                data_dir=str(d / "espeak-ng-data"), lexicon=str(d / "lexicon-us-en.txt"), lang="en-us",
            ),
            num_threads=TTS_THREADS,
        )
    else:
        model = sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(onnx), tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")
            ),
            num_threads=TTS_THREADS,
        )
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model))


def new_vad(silence: float = END_SILENCE, longest: float = MAX_UTTERANCE):
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
    # A lower bar keeps a soft trailing word counted as speech rather than silence.
    cfg.silero_vad.threshold = VAD_THRESHOLD
    cfg.silero_vad.min_silence_duration = silence
    cfg.silero_vad.min_speech_duration = 0.15
    # Past this the detector would split the utterance itself and end the turn early;
    # the mic watchdog owns the time limit instead.
    cfg.silero_vad.max_speech_duration = longest + 30
    cfg.sample_rate = RATE
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=longest + 35)


asr = load_asr()
tts = load_tts()
TTS_RATE = tts.sample_rate
voiceprint = speaker_id.SpeakerCheck(DATA / "voiceprint", MODELS / "speaker" / "nemo_en_titanet_small.onnx")


def transcribe(samples: np.ndarray, keep: bool = True) -> str:
    # The cube sends its mic level as-is; bring speech up to a steady level here.
    samples = samples - samples.mean()
    peak = float(np.abs(samples).max()) or 1.0
    samples = samples * min(0.8 / peak, 30.0)
    if keep:
        try:
            import soundfile
            soundfile.write(DATA / "last_heard.wav", samples, RATE)
        except Exception:
            pass
    s = asr.create_stream()
    s.accept_waveform(RATE, samples)
    asr.decode_stream(s)
    return s.result.text.strip()


def transcribe_timed(samples: np.ndarray) -> tuple:
    """(text, [(sentence, start, end)]) with each sentence's place in seconds into the audio,
    so what the open ear heard can be checked against what was playing at that moment."""
    samples = samples - samples.mean()
    samples = samples * min(0.8 / (float(np.abs(samples).max()) or 1.0), 30.0)
    s = asr.create_stream()
    s.accept_waveform(RATE, samples)
    asr.decode_stream(s)
    r = s.result
    spans, cur, start = [], "", None
    for tok, t in zip(r.tokens, r.timestamps):
        start = t if start is None else start
        cur += tok
        if tok.rstrip().endswith((".", "?", "!")):
            spans.append((" ".join(cur.split()), start, t))
            cur, start = "", None
    if cur.strip():
        spans.append((" ".join(cur.split()), start, r.timestamps[-1] if r.timestamps else 0.0))
    return r.text.strip(), spans


def transcribe_scored(samples: np.ndarray, keep: bool = True) -> tuple:
    """(text, [(word, how sure)]): the transcript and how sure the recognizer was of each word,
    for telling words from a cough it wrote down as one (nonspeech.py)."""
    if samples is None or not len(samples):
        return "", []
    samples = samples - samples.mean()
    samples = samples * min(0.8 / (float(np.abs(samples).max()) or 1.0), 30.0)
    if keep:
        try:
            import soundfile
            soundfile.write(DATA / "last_heard.wav", samples, RATE)
        except Exception:
            pass
    s = asr.create_stream()
    s.accept_waveform(RATE, samples)
    asr.decode_stream(s)
    r = s.result
    return r.text.strip(), nonspeech.scored_words(r.tokens, list(getattr(r, "ys_log_probs", []) or []))


def synthesize(text: str) -> bytes:
    audio = tts.generate(text, sid=VOICE_SID, speed=VOICE_SPEED)
    pcm = soxr.resample(np.asarray(audio.samples, dtype=np.float32), audio.sample_rate, OUT_RATE)
    return (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------- ElevenLabs

# Taken out of the environment, so the Claude sessions started below never inherit it.
EL_KEY = os.environ.pop("ELEVENLABS_API_KEY", "").strip()
EL_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "56bWURjYFHyYyVf490Dp")
EL_VOICE_NAME = os.environ.get("ELEVENLABS_VOICE_NAME", "Emma")
EL_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
EL_RATE = int(os.environ.get("ELEVENLABS_RATE", "24000"))            # asks for pcm_<rate>
EL_HOST = os.environ.get("ELEVENLABS_HOST", "api.elevenlabs.io")
EL_FIRST_AUDIO = float(os.environ.get("ELEVENLABS_FIRST_AUDIO", "1.5"))  # past this, Kokoro says the clause
EL_STALL = float(os.environ.get("ELEVENLABS_STALL", "3"))
EL_FAILS = int(os.environ.get("ELEVENLABS_FAILS", "3"))                 # failures in a row before backing off
EL_BACKOFF = float(os.environ.get("ELEVENLABS_BACKOFF", "300"))
EL_QUOTA_BACKOFF = float(os.environ.get("ELEVENLABS_QUOTA_BACKOFF", "3600"))
# Each voice comes out at its own level; this brings it to Kokoro's, so a reply that mixes the two is even.
EL_GAIN = float(os.environ.get("ELEVENLABS_GAIN", "1.0"))
VOICE_MODE_FILE = DATA / "voice_mode.json"


class ElevenFail(Exception):
    pass


class ElevenLabs:
    """The optional cloud voice. Each clause is streamed and passed to the cube as it
    arrives; anything that stops it from starting in time hands that clause to Kokoro,
    which stays loaded. Failures in a row put it aside for a few minutes."""

    def __init__(self):
        self.on = self._load()
        self.failures = 0
        self.backoff_until = 0.0
        self.fallbacks = 0
        self.streamed = 0
        self.last_fallback = None
        self.quota = None
        self._ssl = None

    def _load(self) -> bool:
        try:
            return bool(json.loads(VOICE_MODE_FILE.read_text()).get("elevenlabs"))
        except (OSError, ValueError):
            return False

    def set(self, on: bool, by: str) -> bool:
        """Switch the mode; True when it changed."""
        self.failures, self.backoff_until = 0, 0.0   # asked for by name: try it again at once
        if on == self.on:
            return False
        self.on = on
        try:
            VOICE_MODE_FILE.write_text(json.dumps({"elevenlabs": on, "at": time.time(), "by": by}))
        except OSError as e:
            log.warning("could not save the voice mode: %s", e)
        log.info("voice mode %s by %s", "elevenlabs" if on else "local", by)
        broadcast(type="voice", elevenlabs=on)
        return True

    def unavailable(self) -> str:
        if not self.on:
            return "off"
        if not EL_KEY:
            return "no api key"
        if time.time() < self.backoff_until:
            return f"backing off for {int(self.backoff_until - time.time())} s"
        return ""

    def failed(self, why: str, text: str):
        self.failures += 1
        if why.startswith("quota"):
            self.backoff_until = time.time() + EL_QUOTA_BACKOFF
        elif self.failures >= EL_FAILS:
            self.backoff_until = time.time() + EL_BACKOFF
        if self.backoff_until > time.time():
            log.warning("elevenlabs: %d failures in a row (%s); Kokoro only for %d s",
                        self.failures, why, int(self.backoff_until - time.time()))

    def fell_back(self, why: str, text: str):
        self.fallbacks += 1
        self.last_fallback = {"at": time.time(), "reason": why}
        log.info("tts fallback to kokoro (%s): %s", why, text[:80])

    def status(self) -> dict:
        return {"elevenlabs": self.on, "speaking_with": "kokoro" if self.unavailable() else "elevenlabs",
                "key": bool(EL_KEY), "voice": EL_VOICE_NAME, "voice_id": EL_VOICE, "model": EL_MODEL,
                "failures_in_a_row": self.failures,
                "backoff_until": self.backoff_until if self.backoff_until > time.time() else None,
                "streamed": self.streamed, "fallbacks": self.fallbacks,
                "last_fallback": self.last_fallback, "quota": self.quota}

    # ---- a minimal HTTP/1.1 client, so a clause starts playing on the first bytes

    async def _open(self, method: str, path: str, body: bytes = b""):
        import ssl
        if self._ssl is None:
            self._ssl = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(EL_HOST, 443, ssl=self._ssl, server_hostname=EL_HOST)
        head = (f"{method} {path} HTTP/1.1\r\nHost: {EL_HOST}\r\nxi-api-key: {EL_KEY}\r\n"
                f"Accept: */*\r\nConnection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n")
        writer.write(head.encode() + body)
        await writer.drain()
        status = int((await reader.readline()).split()[1])
        headers = {}
        while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
            k, _, v = line.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        return status, self._body(reader, headers), writer

    async def _body(self, reader, headers):
        read = lambda n: asyncio.wait_for(reader.read(n), EL_STALL)
        if "chunked" in headers.get("transfer-encoding", ""):
            while True:
                size = int((await asyncio.wait_for(reader.readline(), EL_STALL)).split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    return
                while size > 0:
                    data = await read(min(size, 8192))
                    if not data:
                        raise ElevenFail("connection closed mid-clause")
                    size -= len(data)
                    yield data
                await asyncio.wait_for(reader.readline(), EL_STALL)
        else:
            left = int(headers.get("content-length", "-1"))
            while left != 0:
                data = await read(8192 if left < 0 else min(left, 8192))
                if not data:
                    return
                left -= len(data)
                yield data

    @staticmethod
    def _reason(status: int, raw: bytes) -> str:
        try:
            detail = json.loads(raw).get("detail", {})
        except ValueError:
            detail = {}
        code = detail.get("status", "") if isinstance(detail, dict) else ""
        if code == "quota_exceeded" or "quota" in str(detail).lower():
            return "quota exceeded"
        return f"http {status}" + (f" {code}" if code else "")

    async def stream(self, text: str):
        """Yield 48 kHz s16le audio for one clause as it arrives. ElevenFail before the first
        chunk means nothing was played and Kokoro can take the clause whole."""
        body = json.dumps({"text": text, "model_id": EL_MODEL}).encode()
        path = f"/v1/text-to-speech/{EL_VOICE}/stream?output_format=pcm_{EL_RATE}"
        writer = None
        try:
            try:
                async with asyncio.timeout(EL_FIRST_AUDIO):
                    status, chunks, writer = await self._open("POST", path, body)
                    if status != 200:
                        raw = b""
                        async for c in chunks:
                            raw += c
                            if len(raw) > 4000:
                                break
                        raise ElevenFail(self._reason(status, raw))
                    first = await anext(chunks, b"")
                    if not first:
                        raise ElevenFail("empty response")
            except TimeoutError:
                raise ElevenFail(f"no audio within {EL_FIRST_AUDIO:g} s") from None
            except (OSError, ValueError, IndexError) as e:
                raise ElevenFail(f"connection: {type(e).__name__}: {e}"[:160]) from None
            resampler = soxr.ResampleStream(EL_RATE, OUT_RATE, 1, dtype="float32")
            to_pcm = lambda out: (np.clip(out, -1, 1) * 32767).astype("<i2").tobytes()
            # The first 0.2 s is gathered before anything is sent, so the cube never runs dry
            # waiting on the second chunk; after that every chunk goes out as it lands.
            carry, raw, pending, flowing = b"", first, b"", False
            while raw:
                raw = carry + raw
                cut = len(raw) // 2 * 2
                raw, carry = raw[:cut], raw[cut:]
                out = resampler.resample_chunk(np.frombuffer(raw, dtype="<i2").astype(np.float32) * (EL_GAIN / 32768))
                pending += to_pcm(out)
                if pending and (flowing or len(pending) >= OUT_RATE * 2 // 5):
                    yield pending
                    pending, flowing = b"", True
                try:
                    raw = await anext(chunks, b"")
                except TimeoutError:
                    raise ElevenFail("stalled mid-clause") from None
                except OSError as e:
                    raise ElevenFail(f"connection lost mid-clause: {e}"[:160]) from None
            pending += to_pcm(resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True))
            if pending:
                yield pending
        finally:
            if writer is not None:
                writer.close()

    async def warm(self):
        """A library voice that has not been used for a while can take seconds to start,
        which would hand the first clause to Kokoro. One word, unplayed, wakes it."""
        if self.unavailable():
            return
        t = time.monotonic()
        try:
            async with asyncio.timeout(8):
                status, chunks, writer = await self._open(
                    "POST", f"/v1/text-to-speech/{EL_VOICE}/stream?output_format=pcm_{EL_RATE}",
                    json.dumps({"text": "Hi.", "model_id": EL_MODEL}).encode())
                try:
                    raw = b"".join([c async for c in chunks])
                finally:
                    writer.close()
            if status == 200:
                log.info("elevenlabs warm in %d ms", (time.monotonic() - t) * 1000)
            else:
                log.info("elevenlabs warm-up failed: %s", self._reason(status, raw))
        except Exception as e:
            log.info("elevenlabs warm-up failed: %s", e or type(e).__name__)

    async def refresh_quota(self):
        """What is left this month, for /health. Read at start and every half hour."""
        while True:
            if EL_KEY:
                writer = None
                try:
                    async with asyncio.timeout(10):
                        status, chunks, writer = await self._open("GET", "/v1/user/subscription")
                        raw = b"".join([c async for c in chunks])
                    if status == 200:
                        s = json.loads(raw)
                        used, limit = s.get("character_count", 0), s.get("character_limit", 0)
                        self.quota = {"tier": s.get("tier"), "used": used, "limit": limit,
                                      "left": max(limit - used, 0), "resets": s.get("next_character_count_reset_unix")}
                    else:
                        log.info("elevenlabs subscription read: http %d", status)
                except Exception as e:
                    log.info("elevenlabs subscription read failed: %s", type(e).__name__)
                finally:
                    if writer is not None:
                        writer.close()
            await asyncio.sleep(1800)


eleven = ElevenLabs()
kokoro = {"busy": 0}     # clauses the local voice is making right now


async def tts_stream(text: str, sentence: str | None = None, at: int = 0):
    """One clause as 48 kHz audio chunks: ElevenLabs while it is on and answering in time,
    otherwise Kokoro in one piece. A fallback is never spoken about, only logged. Each voice
    is handed the clause through the pronunciation pass for that voice, read against the
    whole sentence it was cut from."""
    why = eleven.unavailable()
    if not why:
        started = False
        try:
            async for pcm in eleven.stream(pronounce.respell(text, "eleven", sentence, at)):
                started = True
                yield pcm
            eleven.failures = 0
            eleven.streamed += 1
            return
        except ElevenFail as e:
            why = str(e)
            eleven.failed(why, text)
            if started:
                # Part of the clause already played; repeating it in another voice would be worse.
                log.info("elevenlabs failed mid-clause (%s): %s", why, text[:80])
                return
    if why != "off":
        eleven.fell_back(why, text)
    kokoro["busy"] += 1
    try:
        pcm = await asyncio.get_running_loop().run_in_executor(
            None, synthesize, pronounce.respell(text, "kokoro", sentence, at))
    finally:
        kokoro["busy"] -= 1
    yield pcm


# ---------------------------------------------------------------- memory

class Notes:
    """Long-term memory: what survives once a conversation session is retired."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists notes (id integer primary key check (id = 1), text text)")
        self.db.execute("create table if not exists turns (id integer primary key, at real, role text, text text)")
        self.db.commit()

    def text(self) -> str:
        row = self.db.execute("select text from notes where id = 1").fetchone()
        return row[0] if row else ""

    def log(self, role: str, text: str):
        self.db.execute("insert into turns (at, role, text) values (?, ?, ?)", (time.time(), role, text))
        self.db.commit()

    def unfolded(self) -> list[tuple[str, str]]:
        return self.db.execute("select role, text from turns order by id").fetchall()

    def replace(self, text: str):
        self.db.execute(
            "insert into notes (id, text) values (1, ?) on conflict(id) do update set text = excluded.text", (text,)
        )
        self.db.execute("delete from turns")
        self.db.commit()


notes = Notes(DATA / "memory.sqlite3")


async def fold_notes():
    """Fold the turns of a retired session into the long-term notes."""
    turns = notes.unfolded()
    if not turns:
        return
    transcript = "\n".join(f"{r}: {t}" for r, t in turns)
    prompt = (
        "You maintain the long-term memory of a household voice assistant. Merge the existing notes with "
        "the new conversation into one concise list of durable facts: people, preferences, plans, ongoing "
        "topics, anything the user asked to be remembered. Drop small talk. Return only the notes.\n\n"
        f"Existing notes:\n{notes.text() or '(none)'}\n\nConversation:\n{transcript}"
    )
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", NOTES_MODEL, "--tools", "", "--setting-sources", "",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=DATA,
    )
    out, err = await proc.communicate(prompt.encode())
    if proc.returncode == 0 and out.strip():
        notes.replace(out.decode().strip())
        log.info("memory folded (%d turns)", len(turns))
    else:
        log.error("memory fold failed: %s", err.decode()[-500:])


# ---------------------------------------------------------------- Claude

class Claude:
    """One long-lived Claude Code process in stream-json mode. Keeping it alive is
    what makes the first token arrive in about half a second, and its own session
    is the short-term memory."""

    folds = True     # this session's turns feed the long-term notes
    interrupts = True  # an abandoned turn is interrupted (a barge-in), not left to finish

    def __init__(self):
        self.proc = None
        self.lock = asyncio.Lock()
        self.last_used = 0.0
        self.owner = None

    def system_prompt(self) -> str:
        prompt = _render((HERE / "system_prompt.md").read_text())
        remembered = notes.text()
        if remembered:
            prompt += f"\n\nWhat you remember from earlier conversations:\n{remembered}"
        return prompt

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--include-partial-messages", "--verbose",
            "--model", MODEL, "--setting-sources", "user",
            "--permission-mode", "bypassPermissions",
            "--append-system-prompt", self.system_prompt(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=Path.home(), limit=1 << 24,
            env={**os.environ, "RATCHET_OFF": "1", "PEER_REPLY_GUARD_OFF": "1"},
        )
        self.last_used = time.monotonic()
        log.info("claude session started")

    async def retire(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None
        await fold_notes()

    async def ensure(self):
        if not self.proc or self.proc.returncode is not None:
            await self.start()

    async def keep_warm(self):
        """Retire an idle session in the background and start the next one at once,
        so a question never waits on a cold start or on the memory fold."""
        while True:
            await asyncio.sleep(60)
            if self.lock.locked():
                continue
            idle = time.monotonic() - self.last_used
            dead = not self.proc or self.proc.returncode is not None
            if dead or idle > SESSION_IDLE:
                async with self.lock:
                    old, self.proc = self.proc, None
                    if old and old.returncode is None:
                        old.stdin.close()
                        try:
                            await asyncio.wait_for(old.wait(), 10)
                        except asyncio.TimeoutError:
                            old.kill()
                    if self.folds:
                        await fold_notes()
                    await self.start()

    async def ask(self, text: str, owner: str | None = None):
        """Yield ("text", delta) as the reply streams, ("tool", name) when it starts using a tool, and
        ("message_end", stop_reason) when each assistant message finishes. Abandoning the generator mid-reply leaves the rest to be
        drained before the next question, so a barge-in never corrupts the session.
        `owner` is the question this turn answers: work its tools report belongs to it."""
        async with self.lock:
            await self.ensure()
            self.owner = owner
            stamp = time.strftime("%A %-I:%M %p")
            msg = {"type": "user", "message": {"role": "user", "content": f"[{stamp}] {text}"}}
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            await self.proc.stdin.drain()
            done = False
            try:
                while True:
                    line = await self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("claude session ended")
                    m = json.loads(line)
                    if m.get("type") == "stream_event":
                        ev = m["event"]
                        if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                            yield "text", ev["delta"]["text"]
                        elif ev.get("type") == "content_block_stop":
                            yield "block_end", None
                        elif ev.get("type") == "content_block_start" and ev["content_block"].get("type") == "tool_use":
                            yield "tool", ev["content_block"].get("name", "")
                        elif ev.get("type") == "message_delta" and ev.get("delta", {}).get("stop_reason"):
                            yield "message_end", ev["delta"]["stop_reason"]
                    elif m.get("type") == "assistant":
                        # The whole message once written: what each tool was actually asked to do.
                        for block in (m.get("message") or {}).get("content") or []:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                yield "tool_use", block
                    elif m.get("type") == "user":
                        for block in (m.get("message") or {}).get("content") or []:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                yield "tool_result", block
                    elif m.get("type") == "result":
                        done = True
                        if m.get("is_error"):
                            raise RuntimeError(str(m.get("result"))[:300])
                        return
            finally:
                self.owner = None
                self.last_used = time.monotonic()
                if not done and self.proc and self.proc.returncode is None:
                    asyncio.create_task(self.drain())

    async def drain(self):
        """Interrupt whatever the session is doing, then wait for its result line."""
        if self.interrupts and self.proc and self.proc.returncode is None:
            req = {"type": "control_request", "request_id": f"int-{time.time_ns()}",
                   "request": {"subtype": "interrupt"}}
            self.proc.stdin.write((json.dumps(req) + "\n").encode())
        async with self.lock:
            while self.proc and self.proc.returncode is None:
                line = await self.proc.stdout.readline()
                if not line or json.loads(line).get("type") == "result":
                    return


# A small pool, so a new question never waits behind a lookup that is still running.
pool = [Claude() for _ in range(THREADS)]
for extra in pool[1:]:
    extra.folds = False
inflight: dict = {}      # question -> when it was asked, while a thread is still on it


def free_thread() -> Claude:
    return next((c for c in pool if not c.lock.locked()), pool[0])


def shared_context(heard: str) -> str:
    """His new question, after what the other threads have heard and said, so each one
    follows the whole conversation. Earlier exchanges go in a block marked as answered,
    each of his lines with the reply it got; a line of his that never got a reply is
    left out, so nothing older reads as a question still waiting."""
    rows = notes.db.execute("select role, text from turns where at > ? order by id desc limit 16",
                            (time.time() - 1800,)).fetchall()[::-1]
    pairs, asked = [], None
    for role, text in rows:
        text = (text or "").strip()
        if role == "late":
            # A lookup that came back after its question and was said on its own.
            about, _, said = text.partition(" -> ")
            pairs.append((f"(a lookup you were still checking came back) {about}", said))
            asked = None
            continue
        if role == "user":
            asked = text if text and text != heard else None
            continue
        said = text.replace("[interrupted]", "").strip()
        if asked and said and not said.startswith("[superseded"):
            pairs.append((asked, said + (" (cut off)" if "[interrupted]" in text else "")))
        asked = None
    parts = []
    if pairs:
        parts.append("EARLIER EXCHANGES, ALREADY ANSWERED. History only: do not answer, repeat or follow up on "
                     "any of these.\n" + "\n".join(f"- He said: {q}\n  You said: {a}" for q, a in pairs[-6:]))
    pending = [q for q in inflight if q != heard]
    if pending:
        parts.append("BEING ANSWERED RIGHT NOW BY ANOTHER COPY OF YOU. Do not answer these: " + "; ".join(pending))
    parts.append("HIS NEW QUESTION, the only thing to answer:\n" + heard)
    return "\n\n".join(parts)


class Acknowledger(Claude):
    """A small, fast model kept warm beside the main agent. While the agent looks
    things up in silence, this says the one natural line a person would say -
    "Let me check on the template sites." - and is never allowed to state a fact,
    so nothing guessed is ever spoken."""

    folds = False
    interrupts = False   # its turns are a few words long; letting one finish is cheaper than interrupting
    PROMPT = (
        "You voice a home assistant's first reaction while it goes and finds the real answer. Given what "
        "the person just said, reply with one short natural acknowledgement, three to eight words, that "
        "shows you understood what they asked: 'Let me check on the template sites.' 'Sure, pulling that "
        "up.' 'Okay, starting on that now.' Never answer the question, never state or guess any fact, "
        "number, status or name you were not told, never promise an outcome. No quotes, no emoji. "
        "Only the acknowledgement."
    )

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--include-partial-messages", "--verbose",
            "--model", ACK_MODEL, "--tools", "", "--setting-sources", "",
            "--system-prompt", self.PROMPT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=DATA, limit=1 << 22,
            # Thinking first costs it three seconds; without it the line arrives in about half a second.
            env={**os.environ, "MAX_THINKING_TOKENS": "0"},
        )
        self.last_used = time.monotonic()
        self.turns = 0

    async def line(self, heard: str) -> str:
        """The acknowledgement for this utterance, or "" if it isn't ready in time."""
        self.turns = getattr(self, "turns", 0) + 1
        if self.turns > 40 and not self.lock.locked():
            # Its own history is worthless; keep it from growing.
            await self.retire_quietly()
        text = ""
        async with contextlib.aclosing(self.ask(heard)) as deltas:
            async for kind, delta in deltas:
                if kind == "text":
                    text += delta
        text = " ".join(text.split()).strip('"')
        return text if 0 < len(text.split()) <= 12 else ""

    async def retire_quietly(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        await self.start()


acknowledger = Acknowledger()


class Gatekeeper(Acknowledger):
    """Decides whether a transcript was said to the assistant or picked up from the room: while
    a computer is playing sound, and before the words of a wake chime, light or show anything."""

    PROMPT = (
        "You screen what a home voice assistant's microphone picked up without its name: right after it "
        "spoke, or while a nearby computer was playing audio (videos, stories, music, calls). Answer "
        "DIRECTED if the transcript reads like a "
        "person talking to their assistant: a question, a request, an instruction, an answer, or a "
        "correction in a conversation with it. A question or an instruction put to \"you\" about work "
        "being done, how far along it is, or what happens next is DIRECTED, however plain it sounds and "
        "with nothing shown before it: the assistant is the one being asked. Answer OVERHEARD when the "
        "words have nobody in the room to be said to, or are said to someone else: narration, lyrics, a "
        "broadcast, a scripted line, the person talking to someone else in the room or on a call, or two "
        "other people talking to each other. When you are shown what the assistant itself just "
        "said, anything that answers it, accepts it, refuses it, repeats its own wording back or argues "
        "with it is DIRECTED, however short and blunt it sounds. Only the one word."
    )

    async def directed(self, text: str, said_last: str = ""):
        """True, False, or None when it can't say in time."""
        self.turns = getattr(self, "turns", 0) + 1
        if self.turns > 40 and not self.lock.locked():
            await self.retire_quietly()
        out = ""
        asked = f"The assistant just said: {said_last}\n\nTranscript: {text}" if said_last else f"Transcript: {text}"
        async with contextlib.aclosing(self.ask(asked)) as deltas:
            async for kind, delta in deltas:
                if kind == "text":
                    out += delta
        verdict = True if "OVERHEARD" not in out.upper() and "DIRECTED" in out.upper() else \
            False if "OVERHEARD" in out.upper() else None
        log.info("audio gate verdict %s (%r) for: %s", verdict, out.strip()[:40], text[:80])
        return verdict


gatekeeper = Gatekeeper()


class LateTeller(Acknowledger):
    """Started only when needed: words a lookup that came back after its question was
    already answered, under the same speech rules as every answer, or says SKIP."""

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--include-partial-messages", "--verbose",
            "--model", LATE_MODEL, "--tools", "", "--setting-sources", "",
            "--system-prompt", _render((HERE / "system_prompt.md").read_text()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=DATA, limit=1 << 22, env={**os.environ, "MAX_THINKING_TOKENS": "0"},
        )
        self.last_used = time.monotonic()
        self.turns = 0

    async def tell(self, about: str, answer: str, asked: float) -> str:
        """The line to say, or "" when it is no longer worth saying."""
        self.turns = getattr(self, "turns", 0) + 1
        if self.turns > 20 and not self.lock.locked():
            await self.retire_quietly()
        since = notes.db.execute("select role, text from turns where at > ? order by id",
                                 (asked - 5,)).fetchall()
        talk = "\n".join(f"- {'He' if r == 'user' else 'You'}: {(t or '').strip()[:300]}" for r, t in since[-10:])
        out = ""
        prompt = (f"LATE ANSWER. {int(time.time() - asked) // 60} minutes ago you looked into this and said the "
                  f"rest was still being checked: {about}\n\nWhat you found: {answer}\n\n"
                  + (f"What has been said since:\n{talk}\n\n" if talk else "")
                  + "Follow your rules for a late answer.")
        async with contextlib.aclosing(self.ask(prompt)) as deltas:
            async for kind, delta in deltas:
                if kind == "text":
                    out += delta
        out = " ".join(out.split()).strip('"')
        return "" if not out or re.match(r"^\W*SKIP\b", out, re.I) else out


late_teller = LateTeller()


async def tell_late(about: str, answer: str, asked: float):
    """Say a late lookup on its own, the way an announcement is said. It answers a question
    he asked, so it is never dropped for being old news: a line the gate refuses is worded
    again once, and only a second refusal keeps it unsaid, logged as an error. It is dropped
    only when the same answer already reached him since he asked: the chat that answered can
    read it out itself through /say while relay.py is still holding the lookup open. Then the
    two are one late answer, said once (late_find)."""
    qid = new_qid()
    if (item := late_find(answer)) is not None:
        late_join(item, "/late")
        return
    # Every sentence of the answer already said word for word since he asked: nothing to word.
    kept, refused = await precheck(answer, asked, qid, "late", use_judge=False, quiet=True)
    if not kept:
        if refused:
            repeat_refused(refused[0], repeats.Line(" ".join(answer.split()), 0, qid, "", "content", "late"),
                           {"question": about}, asked)
        notes.log("late", f"{about} -> (already said)")
        return
    try:
        line = voice_rules(await asyncio.wait_for(late_teller.tell(about, answer, asked), 60), True)
    except Exception as e:
        log.info("could not word a late answer (%s); saying the answer itself", e)
        line = ""
    if not line:
        # The teller skipped it, but he asked: say the answer's first two sentences.
        line = voice_rules(" ".join(re.findall(r"[^.!?]+[.!?]", " ".join(answer.split()))[:2]) or answer[:300], True)
    line, _ = await precheck(line, asked, qid, "late")
    if not line:
        notes.log("late", f"{about} -> (already said)")
        return
    for attempt in range(2):
        prog = in_progress()
        verdict = await speech_gate.check(line, about, [], prog, "late answer")
        gate_log(verdict, line, qid, "late", attempt, about, [], prog)
        if verdict["verdict"] != "broken":
            break
        if attempt == 1:
            log.error("late answer not said: refused twice by the speech gate: %s", line[:160])
            return
        try:
            line = voice_rules(await asyncio.wait_for(late_teller.tell(
                about, answer + "\n\n" + speech_gate.retry_prompt(verdict, line, prog), asked), 60), True) or line
        except Exception as e:
            log.error("late answer not said: could not reword it (%s): %s", e, line[:160])
            return
    # The chat may have read the same answer out through /say while this was being worded.
    if (item := late_find(answer, line)) is not None:
        late_join(item, "/late")
        return
    log.info("late answer: %s", line[:120])
    late_add(LateItem(qid, line, answer, "late", asked, about, asked))


def last_said(within: float = 150.0) -> str:
    """Sunday's own last words, while they are recent enough that the next thing the mic
    picks up is probably an answer to them. The gate judges a lone transcript, and a
    short answer to a question Sunday asked reads like a fragment without it."""
    row = notes.db.execute("select text from turns where role = 'assistant' and at > ? order by id desc limit 1",
                           (time.time() - within,)).fetchone()
    text = (row[0] or "").replace("[interrupted]", "").strip() if row else ""
    return "" if text.startswith("[superseded") else text[-500:]


async def not_for_sunday(heard: str, followup: bool, playing: bool) -> str:
    """Why this transcript should be dropped rather than answered, or "" to answer it."""
    words = re.findall(r"[a-z0-9']+", heard.lower())
    if followup and words and all(w in FILLER for w in words):
        return "filler in the follow-up window"
    if not (AUDIO_GATE and playing):
        return ""
    if "sunday" in words:
        return ""
    if all(w in FILLER for w in words):
        return "filler while computer audio playing"
    if len(words) < GATE_MIN_WORDS:
        return f"under {GATE_MIN_WORDS} words while computer audio playing"
    if GATE_CLASSIFY:
        try:
            verdict = await asyncio.wait_for(gatekeeper.directed(heard, last_said()), 6)
        except Exception as e:
            log.info("audio gate check failed (%s); answering", e)
            verdict = None
        if verdict is False:
            return "not said to Sunday while computer audio playing"
    return ""


# ---------------------------------------------------------------- whose voice

speaker_last: dict = {}        # the last decision, for /health
passive: dict = {}             # his last answered turn, kept as a sample once he follows it up


def decode_pcm(b64) -> np.ndarray | None:
    """16 kHz s16le mono sent as base64, as float samples."""
    if not b64:
        return None
    try:
        import base64
        raw = base64.b64decode(b64)
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768
    except (ValueError, TypeError):
        return None


def own_playback(info: dict | None) -> bool:
    """The cube's echo report: it was playing, and what its mic heard at the wake left no more
    residual after echo cancellation than its own voice does. Its own speaker said "Sunday"."""
    e = (info or {}).get("echo") or {}
    return bool(e.get("playing")) and e.get("excess_db") is not None and float(e["excess_db"]) < OWN_PLAYBACK_DB


async def speaker_score(audio) -> dict:
    """The speaker check on one stretch, off the event loop. Never raises."""
    if not SPEAKER or audio is None or not len(audio):
        return {"score": None, "verdict": "off", "speech_s": 0.0, "ms": 0, "emb": None}
    try:
        return await asyncio.get_running_loop().run_in_executor(None, voiceprint.check, audio)
    except Exception as e:
        log.exception("speaker check failed")
        return {"score": None, "verdict": "error", "speech_s": 0.0, "ms": 0, "emb": None, "error": str(e)[:120]}


def speaker_action(spk: dict, barge: bool = False, playback: bool = False) -> tuple:
    """("pass" | "borderline" | "reject", why) for a scored stretch: what enforcing does with it.
    Anything the check cannot score passes; too little speech is borderline, never a reject."""
    v = spk.get("verdict")
    st = voiceprint.state
    if v == "accept":
        return "pass", "his voice"
    if playback and v in ("reject", "borderline", "short"):
        # Never on its own: his voice from across the room is no louder than its own residual.
        return "reject", "its own playback: no more than its own voice's residual, and not plainly his voice"
    if v in ("off", "error", "unknown", None):
        return "pass", f"no voiceprint verdict ({v})"
    sc, rej, acc = spk["score"], st.get("reject"), st.get("accept")
    if v == "short":
        return "borderline", f"{spk.get('speech_s', 0):.1f} s of speech is too little to tell"
    if v == "reject":
        if barge and sc >= rej - SPEAKER_BARGE_SLACK:
            return "borderline", f"{sc:.2f} under {rej:.2f}, heard over its own voice"
        return "reject", f"{sc:.2f} under {rej:.2f}: not his voice"
    return "borderline", f"{sc:.2f} between {rej:.2f} and {acc:.2f}"


def speaker_log(kind: str, device: str, qid, spk: dict, action: str, why: str, done: str, text: str = "",
                extra: dict | None = None) -> dict:
    """One line per decision, with the score, and the same as a dict for the speech log."""
    rec = {"score": spk.get("score"), "verdict": spk.get("verdict"), "action": action, "why": why,
           "mode": voiceprint.state.get("mode"), "reject_at": voiceprint.state.get("reject"),
           "accept_at": voiceprint.state.get("accept"), "speech_s": spk.get("speech_s"), "ms": spk.get("ms"),
           "done": done, **(extra or {})}
    log.info("speaker check %s on %s: score %s, %s, %s (%s) -> %s [%s, %.1f s of speech, %d ms]%s", kind, device,
             "none" if spk.get("score") is None else f"{spk['score']:.3f}", spk.get("verdict"), action, why, done,
             rec["mode"], spk.get("speech_s") or 0.0, spk.get("ms") or 0,
             f": {' '.join(text.split())[:90]}" if text else "")
    speaker_last.clear()
    speaker_last.update({"at": time.time(), "kind": kind, "device": device, "qid": qid, "text": text[:120], **rec})
    return rec


directed_asked: dict = {}     # the words lately put to the judge: when, and its verdict on them


async def directed(heard: str) -> bool | None:
    """The directed-or-overheard judge on a transcript: was it said to Sunday? The same words
    asked again within half a minute (while he spoke, then once he stopped) are judged once."""
    async def ask():
        try:
            return await asyncio.wait_for(gatekeeper.directed(heard, last_said()), 6)
        except Exception as e:
            log.info("directed check failed (%s)", e)
            return None
    now, key = time.monotonic(), " ".join(heard.lower().split())
    for k in [k for k, (at, _) in directed_asked.items() if now - at > 30]:
        del directed_asked[k]
    if key not in directed_asked:
        directed_asked[key] = (now, asyncio.ensure_future(ask()))
    return await asyncio.shield(directed_asked[key][1])


def mode_changed(before: str):
    """The voiceprint switched mode by itself: tell every device whether to hold its chime."""
    now = voiceprint.state.get("mode")
    if now == before:
        return
    st = voiceprint.state
    log.info("speaker check: now %s (%s; reject under %s, accept from %s, %d samples)", now, st.get("why"),
             st.get("reject"), st.get("accept"), len(voiceprint.samples))
    for c in list(cubes):
        asyncio.get_running_loop().create_task(c.send(type="verify", hold=voiceprint.enforcing and SPEAKER))


def keep_sample(spk: dict, meta: dict, source: str):
    before = voiceprint.state.get("mode")
    try:
        what = voiceprint.add_sample(spk.get("emb"), meta, source)
    except Exception:
        log.exception("could not keep a voice sample")
        return
    log.info("voiceprint: %s sample from %s (%s): %s", source, meta.get("device", "?"), meta.get("text", "")[:60], what)
    mode_changed(before)


def followed_up(heard_spk: dict):
    """A new turn of his: the answered turn before it, if recent, was his, so it is kept."""
    p = passive.pop("pending", None)
    if not p or time.time() - p["answered_at"] > FOLLOWED_UP or heard_spk.get("verdict") == "reject":
        return
    keep_sample(p["spk"], p["meta"], "passive")


# ---------------------------------------------------------------- sentence splitting

SENTENCE_END = re.compile(r"[.!?;:](?:[\"')\]]*)\s+")


def take_speakable(buf: str, first: bool) -> tuple[str, str]:
    """Split off text that is safe to speak now. The first chunk goes out at the
    earliest clause, because time to first audio is the latency people hear."""
    buf = re.sub(r"([.!?])(?=[A-Z])", r"\1 ", buf)
    m = SENTENCE_END.search(buf)
    if m and (first or m.end() >= 30):
        return buf[: m.end()], buf[m.end():]
    if first and len(buf) > 50:
        cut = buf.rfind(", ")
        if cut > 15:
            return buf[: cut + 2], buf[cut + 2:]
    return "", buf


ACK_OPENER = re.compile(
    r"^\s*(?:got it|understood|will do|sure(?: thing)?|okay|ok|alright|all right|no problem|of course|"
    r"absolutely|great|perfect|on it|sounds good|you bet)\b[\s,.!:;\u2014-]*", re.I)
NARRATION = re.compile(
    r"^\s*(?:let me|let's|i'll (?:now |just |go |quickly )?(?:check|look|search|try|update|find|pull|run|grab|"
    r"see|ask|start|get|open|dig|read|fix|retry|verify|confirm)|i'm (?:going to|gonna|now |just )?(?:check|look|"
    r"search|updat|tr[yi]|pull|run|fetch|grab|ask|start|dig|read|fix|verif|confirm)\w*|now,? (?:i'll|let me|i'm|"
    r"\w+ing)|first,? (?:i'll|let me)|next,? (?:i'll|let me)|(?:checking|looking|searching|updating|retrying|"
    r"trying|pulling|running|fetching|grabbing|asking|starting|digging|reading|fixing|verifying|confirming)\b)", re.I)
STARTED_CHAT = re.compile(
    r"[^.!?]*\bI(?:'ve| have| just)? (?:started|opened|kicked off|spun up|created|launched|begun|set up) "
    r"(?:a |an |up a )?(?:new |fresh |separate )?(?:chat|session|conversation|thread)\b[^.!?]*[.!?]?", re.I)
SESSION_LINE = "I'm on it, and I'll let you know."
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def voice_rules(text: str, first: bool) -> str:
    """The spoken rules, enforced here rather than trusted to the prompt: no opening
    acknowledgement, no narration of what is being done, the session wording for
    handed-off work, and nothing that only makes sense written down."""
    # A canvas is for his screen and never for the voice, whichever way it arrived.
    if "canvas" in text.lower():
        text = canvases.split(text)[0]
    # SKIP is a word for the code, never for him: on its own it means say nothing.
    text = re.sub(r"(?:^|(?<=[.!?\s]))\W*\bSKIP\b\W*(?=\s|$)", " ", text).strip()
    if not text:
        return ""
    text = re.sub(r"https?://(?:www\.)?([^/\s]+)\S*|www\.([^/\s]+)\S*",
                  lambda m: (m.group(1) or m.group(2)).rstrip(".,"), text)             # a link becomes its site
    text = re.sub(r"(?<![\w.])(?:~|\.{1,2})?/(?:[\w.@-]+/)*[\w.@-]+|`[^`]*`", "\x00", text)  # paths, code
    text = re.sub(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+(.*?)[.!?]?\s*$", r"\1.", text, flags=re.M)  # list -> sentences
    text = re.sub(r"\*\*|__|##+", "", text)
    text = EMOJI.sub("", text)
    text = re.sub(r"([.!?])(?=[A-Z])", r"\1 ", text)                              # "there.Now" -> "there. Now"
    text = STARTED_CHAT.sub(" " + SESSION_LINE + " ", text)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if first and not kept:
            while True:
                stripped = ACK_OPENER.sub("", sentence, count=1)
                if stripped == sentence:
                    break
                sentence = stripped[:1].upper() + stripped[1:]
        if "\x00" in sentence:
            # A path or code fragment can't be said; keep the sentence only if it stands without it.
            rest = re.sub(r"\s*\x00\s*", " ", sentence)
            if len(re.findall(r"[A-Za-z']+", rest)) < 7 or re.search(r"\b(?:in|at|to|for|and|from|see|run)\s*[.!?,]", rest):
                continue
            sentence = rest
        sentence = re.sub(r"\s+([.!?,])", r"\1", sentence)
        if not sentence.strip() or NARRATION.match(sentence):
            continue
        if sentence.strip() == SESSION_LINE and SESSION_LINE in " ".join(kept):
            continue
        kept.append(sentence)
    return speakable(" ".join(kept))


def speakable(text: str) -> str:
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    return " ".join(text.split())


def clauses(text: str) -> list[str]:
    """Split a sentence at commas, semicolons, colons and dashes into pieces of at
    least three words, so a slow voice can start on the first one sooner."""
    if not TTS_CLAUSES:
        return [text]
    pieces = [p for p in re.split(r"(?<=[,;:])\s+|\s+(?=[-–—]\s)", text) if p.strip()]
    out: list[str] = []
    for p in pieces:
        if out and (len(out[-1].split()) < 3 or len(p.split()) < 3):
            out[-1] += " " + p
        else:
            out.append(p)
    return out or [text]


# ---------------------------------------------------------------- one cube

class CubeSession:
    def __init__(self, ws):
        self.ws = ws
        self.device = "cube"
        self.vad = None
        self.listen_started = 0.0
        self.last_audio = 0.0
        self.heard_speech = False
        self.turns: set = set()        # questions being answered, possibly several at once
        self.voice = asyncio.Lock()    # one answer speaks at a time
        self.t_end = 0.0
        self.unsaid = None
        self.connected_at = time.monotonic()
        self.announcing: set = set()   # announcement tasks, cut off like an answer when he speaks
        self.play_until = 0.0          # when the cube should finish playing what it has been sent
        self.ear = False               # a secondary ear (the Mac): it defers to the cube
        self.away = False              # an ear away from home, out of the cube's hearing
        self.hw_muted = False          # the cube's mute button: it hears nothing
        self.trigger_until = 0.0       # it is saying "Sunday" or "stop" itself until then
        self.light = False             # the cube's ring is lit
        self.barge = None              # what was being said when he woke it mid-speech
        self.last_utt = None           # the utterance it was last sent audio for
        self.successor = None          # the connection the same cube came back on
        self.lost_at = 0.0             # when this connection dropped
        self.box = Outbox()            # speech sent to this cube, shared with its successors
        self.cursor = 0                # the next outbox frame this connection has to send
        self.pump_lock = asyncio.Lock()
        self.resume_have = None        # from "hello": the last sentence the cube holds whole
        self.timeline = deque(maxlen=64)   # (start, end, text) of each clause it was sent, by when it plays
        self.over_vad = None           # the open ear: its mic while it talks, with no wake word
        self.over_audio = deque()      # (first sample, samples) of the open ear, the last OVER_KEEP seconds
        self.over_n = 0                # samples heard by the open ear since it opened
        self.over_base = 0             # the sample the open ear's detector counts from
        self.over_seg = None           # what he is saying over it now, until it is decided
        self.over_stats = {"from": 0, "speech": 0, "own": 0, "read": 0, "partials": 0}
        self.over_levels = deque(maxlen=500)   # dBFS of each 20 ms the open ear heard, the last 10 s
        self.wake_info = None          # the last wake word's report: prob, level, the cube's echo figures
        self.preroll = None            # the wake word itself, the 1.5 s before it fired
        self.hold = False              # this wake's chime, ring and overlay wait for the speaker check
        self.spk_ok = False            # the speaker check has cleared this wake
        self.spk_busy = False
        self.spk_early = None          # the last score taken while he was still talking
        self.spk_next = SPEAKER_EARLY_S   # seconds heard at which a held wake is scored next
        self.enroll_until = 0.0        # the next follow-up window here is his enrollment, until then
        self.followup = False
        self.qid = None
        self.by_word = False           # this listen began with the wake word, which must be heard in it
        self.words_ok = False          # words have been heard: only then does it chime, light or show
        self.words_busy = False
        self.duck_later = []           # devices talking at the wake, turned down once words are heard
        self.sounding = None           # its speaker heard in the room, as it reports; None: it never has
        self.heard_until = -1e9        # when its speaker was last heard in the room, echo tail included
        self.turned_down = 0.0         # when what it was saying was last turned down or stopped
        self.turned_down_why = ""
        self.listen_due = 0.0          # when a reply last asked it for a follow-up window

    async def send(self, **msg):
        kind = msg.get("type")
        if kind == "stop_audio":
            self.box.forget()          # what was stopped is never sent again after a reconnect
        dev = self.live()
        if kind == "listen":
            dev.listen_due = time.monotonic()
        elif kind in TURNS_DOWN and (kind != "duck" or msg.get("on")) and dev.is_talking():
            # From here what it is saying plays quieter, or not at all: not heard in full.
            dev.turned_down, dev.turned_down_why = time.monotonic(), TURNS_DOWN[kind]
        try:
            await dev.ws.send(json.dumps(msg))
        except websockets.ConnectionClosed:
            pass

    def live(self) -> "CubeSession":
        """The connection this cube is on now: itself, or the one it came back on."""
        s = self
        while s.successor is not None:
            s = s.successor
        return s

    def is_open(self) -> bool:
        return self.ws.state is State.OPEN

    async def speech(self, data, seq=None):
        """Send one frame of speech through the outbox. It never waits on a lost link: the
        frame stays in the outbox, the voice goes on being made, and the cube is sent it
        when it reconnects."""
        self.box.add(seq, data)
        dev = self.live()
        if dev.is_open():
            try:
                await dev.pump()
            except websockets.ConnectionClosed:
                pass

    async def pump(self):
        async with self.pump_lock:
            await self._pump()

    async def _pump(self):
        self.cursor = max(self.cursor, self.box.base, self.box.floor)
        while self.cursor < self.box.end:
            await self.ws.send(self.box.frame(self.cursor))
            self.cursor += 1

    def adopt(self, old: "CubeSession"):
        """The same cube, back on a new connection: take over what the old one was saying
        and doing, and drop the old link at once rather than waiting out its keepalive."""
        old.successor = self
        old.turns |= self.turns
        self.turns = old.turns
        old.announcing |= self.announcing
        self.announcing = old.announcing
        if not self.voice.locked():
            self.voice = old.voice
        self.box = old.box
        self.last_utt, self.unsaid = old.last_utt, old.unsaid
        self.timeline = old.timeline
        self.play_until = max(self.play_until, old.play_until)
        self.trigger_until = max(self.trigger_until, old.trigger_until)
        self.turned_down, self.turned_down_why = max((self.turned_down, self.turned_down_why),
                                                     (old.turned_down, old.turned_down_why))
        self.listen_due = max(self.listen_due, old.listen_due)
        cubes.discard(old)
        if orphans.get(old.device) is old:
            orphans.pop(old.device)
        if old.is_open():
            old.ws.transport.abort()
        lost = old.lost_at or time.monotonic()
        live = sum(1 for t in self.turns if not t.done()) + sum(1 for t in self.announcing if not t.done())
        start = self.box.resume_point(self.resume_have, lost)
        self.cursor = start if start is not None else self.box.end
        log.info("%s back after %.1f s: %d answer(s) carried over, %s", self.device, time.monotonic() - lost, live,
                 f"sending again from sentence {self.box.seq_at(start)}" if start is not None else "nothing to send again")
        if start is not None:
            asyncio.get_running_loop().create_task(self.replay())

    async def replay(self):
        try:
            async with self.pump_lock:
                await self.ws.send(json.dumps({"type": "speak_start", "rate": OUT_RATE}))
                n0 = self.cursor
                await self._pump()
                secs = self.box.audio_seconds(n0, self.cursor)
            self.play_until = max(time.monotonic(), self.play_until) + secs
            if not self.voice.locked():
                # Nothing more is being said: let the cube finish and settle as it would have.
                await self.speech(json.dumps({"type": "speak_end"}))
            log.info("%s sent %.1f s of speech again", self.device, secs)
        except websockets.ConnectionClosed:
            pass

    async def orphan_expiry(self):
        """A cube that has not come back within RESUME_WAIT: what it was doing ends."""
        await asyncio.sleep(RESUME_WAIT)
        if self.successor is None:
            if orphans.get(self.device) is self:
                orphans.pop(self.device)
            for t in list(self.turns) + list(self.announcing):
                if not t.done():
                    t.cut_cause = "disconnect"
                    t.cancel()

    async def run(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    if self.vad is not None:
                        await self.on_audio(msg)
                    elif self.over_vad is not None:
                        self.on_over_audio(msg)
                    continue
                data = json.loads(msg)
                kind = data.get("type")
                if kind == "hello":
                    self.device = data.get("device", self.device)
                    self.ear = data.get("role") == "ear"
                    self.away = bool(data.get("away"))
                    self.hw_muted = bool(data.get("hw_muted"))
                    log.info("%s connected: %s%s", "ear" if self.ear else "cube", self.device,
                             " (away)" if self.away else "")
                    # words: every wake word is checked for words before it chimes, lights or shows.
                    await self.send(type="verify", hold=voiceprint.enforcing and SPEAKER, words=True)
                    if not self.ear:
                        r = data.get("resume") or {}
                        self.resume_have = (int(r["have"]) if r.get("boot") == BOOT and r.get("have") is not None
                                            else 0 if r.get("boot") else None)
                        for old in [c for c in list(cubes) + list(orphans.values())
                                    if c is not self and not c.ear and c.device == self.device and c.successor is None]:
                            self.adopt(old)
                elif kind == "presence":
                    self.away = bool(data.get("away"))
                    log.info("%s is %s", self.device, "away from home" if self.away else "at home")
                elif kind == "hw_mute":
                    self.hw_muted = bool(data.get("muted"))
                    log.info("%s mute button %s", self.device, "on" if self.hw_muted else "off")
                elif kind == "unmute":
                    set_muted(False, data.get("by", "cube"))
                elif kind == "light":
                    self.light = bool(data.get("on"))
                    broadcast(type="light", on=light_on())
                elif kind == "playing":
                    # Its speaker started or stopped being heard in the room, by its loopback and
                    # the echo tail it measured: the real end of its voice, not the clock here.
                    was, self.sounding = self.sounding, bool(data.get("on"))
                    if was and not self.sounding:
                        self.heard_until = time.monotonic()
                elif kind == "ear":
                    self.over_open(bool(data.get("on")))
                elif kind == "stopped":
                    # He said "stop" or pressed the button: the cube has gone quiet, so stop
                    # sending it the rest of whatever was being said.
                    log.info("stopped on %s by %s", self.device, data.get("by", "stop"))
                    self.box.forget()
                    self.cut_off("stop")
                    self.play_until = time.monotonic()
                elif kind == "preroll":
                    # The Mac's wake word itself, sent once the wake was cleared.
                    self.preroll = decode_pcm(data.get("pcm"))
                    if self.hold and not self.spk_ok and self.vad is not None:
                        asyncio.get_running_loop().create_task(self.early_check("wake word"))
                    if self.by_word and not self.words_ok and self.vad is not None:
                        asyncio.get_running_loop().create_task(self.word_check())
                elif kind == "wake":
                    word = data.get("word", True)
                    echo = data.get("echo") or {}
                    log.info("wake from %s%s%s%s", self.device, "" if word else " (follow-up window)",
                             f" p={data['prob']} {data['level']} dBFS" if data.get("prob") is not None
                             and data.get("level") is not None else "",
                             f", echo {'playing' if echo.get('playing') else 'quiet'} residual "
                             f"{echo.get('excess_db')} dB over its own" if echo else "")
                    if word:
                        self.wake_info = {k: data.get(k) for k in ("prob", "level", "sure", "floor", "echo", "by", "lead")}
                        self.preroll = decode_pcm(data.get("preroll"))
                    if word and self.ear:
                        # Never awaited here: audio held on the Mac only follows the verdict.
                        asyncio.get_running_loop().create_task(self.ear_wake(data.get("sure", True)))
                        continue
                    if self.ear and not self.away and (hearing := cubes_hearing()):
                        # Its follow-up window would hear the cube's voice as his.
                        await self.standdown(f"follow-up window: {', '.join(c.device for c in hearing)} is talking "
                                             "and hears him itself")
                        continue
                    if word and not self.ear and await self.cube_yields():
                        continue
                    await self.on_wake(word, data.get("sure", word))
        except websockets.ConnectionClosed as e:
            log.info("%s link lost: %s", self.device, e)
        finally:
            self.lost_at = time.monotonic()
            self.vad = None
            self.over_open(False)
            if self.successor is not None:
                pass                    # its new connection carries on with everything
            elif self.ear:
                for t in list(self.turns):
                    t.cancel()
            else:
                # Its answers keep going and wait in the outbox for it to come back.
                orphans[self.device] = self
                asyncio.get_running_loop().create_task(self.orphan_expiry())

    # ---- which device answers

    async def standdown(self, why: str):
        log.info("%s stands down: %s", self.device, why)
        if self.vad is not None:
            self.vad = None
        if self.barge is not None:
            await self.release_barge(self.barge, {"decision": "continue", "reason": "stood down: " + why,
                                                  "by": "code", "ms": 0}, "", getattr(self, "qid", None))
        await self.send(type="standdown", why=why)
        if any(c.barge and self in c.barge["devices"] for c in cubes if c is not self):
            # The other device took this wake and he is talking over this one: stay ducked.
            await self.send(type="duck", on=True)

    async def cube_yields(self) -> bool:
        """A cube wake: the cube has priority, unless the Mac was already cleared for this
        same word, or the wake is the Mac saying "Sunday" in a reply."""
        now = time.monotonic()
        arb.cube_woke_at = now
        arb.cube_woke.set()
        if now - arb.ear_cleared_at < EAR_LATE_CUBE:
            mac = arb.ear_dev
            if mac is not None and mac.vad is not None and not mac.words_ok:
                # The Mac went ahead on a sound or a far-off word and has heard nothing
                # yet; the cube heard the name itself, so he is by the cube: it answers.
                await mac.standdown("the cube heard it, and the Mac had heard no words yet")
            else:
                await self.standdown("the Mac already took this wake")
                return True
        if others_saying_trigger(self):
            await self.standdown("heard another device say its own trigger word")
            return True
        return False

    async def ear_wake(self, sure: bool):
        """A wake from the Mac: hold it until the cube has had its chance."""
        now = time.monotonic()
        # The open ears' stretches are only taken over once the Mac goes ahead (on_wake): a
        # wake that stands down here leaves them to finish what they heard.
        if others_saying_trigger(self):
            await self.standdown("another device is saying its own trigger word")
            return
        cubes_here = [c for c in speakers() if not c.ear and not c.hw_muted]
        if self.away:
            why = "away from home"
        elif not cubes_here:
            why = "no cube listening"
        else:
            if hearing := cubes_hearing(now):
                await self.standdown(f"{', '.join(c.device for c in hearing)} could be heard when the word was said")
                return
            if now - arb.cube_woke_at < EAR_CUBE_BEFORE:
                await self.standdown("the cube heard it")
                return
            arb.cube_woke.clear()
            try:
                await asyncio.wait_for(arb.cube_woke.wait(), EAR_WAIT)
                await self.standdown(f"the cube heard it {(arb.cube_woke_at - now) * 1000:.0f} ms later")
                return
            except asyncio.TimeoutError:
                why = f"the cube did not hear it in {EAR_WAIT * 1000:.0f} ms"
            if hearing := cubes_hearing():
                await self.standdown(f"{', '.join(c.device for c in hearing)} started talking and hears him itself")
                return
        log.info("%s goes ahead: %s", self.device, why)
        arb.ear_cleared_at = time.monotonic()
        arb.ear_dev = self
        await self.on_wake(True, sure)
        # hold: the ding waits for the brain's "speech", which it sends once the wake word is
        # heard in what the Mac sends (and, enforcing, once the voice is his).
        await self.send(type="go", hold=True)

    # ---- listening

    async def on_wake(self, word: bool = True, sure: bool = True):
        # Waking it while it talks cuts that answer, or an announcement, off. A question
        # still being looked into carries on until he asks a newer one.
        if not word and self.vad is not None:
            log.info("follow-up window already open; ignoring the second")
            return
        over_woken()
        if not word and muted:
            await self.send(type="stop_listening")
            await self.send(type="idle")
            return
        if not word and AUDIO_GATE and computer_playing() and time.monotonic() >= self.enroll_until:
            log.info("discarded follow-up window: computer audio playing on %s", ", ".join(playing_machines()))
            await self.send(type="stop_listening")
            await self.send(type="idle")
            return
        # He spoke to Sunday while it talks: nothing is cut off yet. Every device saying
        # something keeps saying it, ducked, until his words show whether they need it to
        # stop (barge.py). This device ducks itself; the others are told to.
        # Words first: a cough turns nothing down (heard_words).
        talking = [d for d in [self] + [c for c in cubes if c is not self] if d.is_talking()]
        self.open_mic(followup=not word, sure=word and sure)
        self.duck_later = [d for d in talking if d is not self]
        if self.words_ok:
            await self.duck_others()
        self.barge = self.barge_context(talking) if talking else None
        if self.barge:
            log.info("barge-in on %s while %s talking: ducked once words are heard, not cut", self.device,
                     ", ".join(d.device for d in talking))
        if self.by_word and self.preroll is not None:
            asyncio.get_running_loop().create_task(self.word_check())
        if self.hold and self.preroll is not None:
            # The wake word alone may already be plainly his: then the chime comes at once.
            asyncio.get_running_loop().create_task(self.early_check("wake word"))

    async def early_check(self, what: str):
        """While a held wake listens: score what there is so far, and clear it the moment it
        is plainly his voice. Anything less waits for the whole utterance, unseen and unheard."""
        if self.spk_busy or self.spk_ok or not self.hold:
            return
        self.spk_busy = True
        qid = self.qid
        try:
            audio = self.preroll if what == "wake word" else self.wake_audio()
            spk = await speaker_score(audio)
            self.spk_early = spk
            if self.qid != qid or self.vad is None:
                return
            action, why = speaker_action(spk, barge=bool(self.barge), playback=own_playback(self.wake_info))
            if spk.get("verdict") == "accept" and action == "pass":
                log.info("speaker check (%s) on %s: %.3f, his voice: chime %.0f ms after the wake", what, self.device,
                         spk["score"], (time.monotonic() - self.listen_started) * 1000)
                await self.verified()
            else:
                log.info("speaker check (%s) on %s: %s %s, holding the chime (%s)", what, self.device,
                         spk.get("score"), spk.get("verdict"), why)
        finally:
            self.spk_busy = False

    async def verified(self):
        """The voice is his: once words have been heard too, the device chimes and lights,
        and the overlay shows."""
        if self.spk_ok:
            return
        self.spk_ok = True
        if self.words_ok:
            await self.show()

    async def word_check(self):
        """A wake word: is "Sunday" itself in the second before it fired? Then the words he
        goes on to say clear it (words_in). Either way nothing chimes, lights, shows or turns
        down until words of his are heard (partial()): a cough, a click, a dish set down or a
        television never get that far."""
        if self.words_ok or self.words_busy or self.preroll is None:
            return
        self.words_busy = True
        qid, began = self.qid, time.monotonic()
        try:
            text, scored = await asyncio.get_running_loop().run_in_executor(
                None, transcribe_scored, self.preroll[-int(WORD_CHECK_S * RATE):], False)
        except Exception:
            log.exception("wake word check failed")
            return
        finally:
            self.words_busy = False
        if self.qid != qid or self.vad is None or self.words_ok:
            return
        ms = (time.monotonic() - began) * 1000
        named = nonspeech.wake_word_heard(text, scored)
        if named and nonspeech.wake_word_heard(said_lately(began - 3.0)):
            named = False      # a word like its name, in what a device was just saying itself
        if named:
            # Its name alone is not a request: nothing chimes until his words are in (words_in).
            log.info("wake word heard on %s in %.0f ms: %s; nothing chimes until his words are in",
                     self.device, ms, text)
            self.named = True
        elif self.ear and self.barge and self in self.barge["devices"]:
            # Woken while it talks here, with no name in the wake word: its own reply, every time
            # one has been read back. The mic closes now rather than holding the open ear off for
            # seconds, or still being open when the reply ends and he goes on without its name.
            # Words of his over the reply are the open ear's to hear.
            log.info("no wake word in the wake on %s over its own reply (%.0f ms, %r): the mic closes",
                     self.device, ms, text)
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": text,
                        "discarded": "no words: no name in a wake over its own reply"})
            self.vad = None
            await self.release_barge(self.barge, {"decision": "continue", "reason": "no name over its own reply",
                                                  "by": "code", "ms": 0}, "", qid)
            await self.send(type="stop_listening")
            await self.send(type="idle")
            broadcast(type="idle", id=qid)
        else:
            # The cube's own second rarely holds its name even when he said it, so real words
            # after the wake are enough there; on the Mac its name has to turn up in them.
            log.info("no wake word in the wake on %s (%.0f ms, %r): nothing chimes, lights or shows until "
                     "words are heard", self.device, ms, text)

    async def words_in(self, text: str, scored: list):
        """A running transcript of a listen not yet cleared. A plain stop or mute clears it at
        once. Anything else takes real words besides its name: after its name in the wake word
        or said alone at the Mac, or with its name in what he goes on to say. On the cube, whose
        own second rarely holds its name, and in a follow-up window, real words are enough. Then
        those words must read as his and said to Sunday (words_for_sunday)."""
        plain = " ".join(text.split())
        if MUTE_SAID.match(plain) or STOP_SAID.match(plain) or bargein.STOP.match(plain):
            await self.heard_words("a stop")
            return
        if not words_past_name(text, scored):
            return
        if self.named or self.lone:
            why = "words after its name"
        elif nonspeech.wake_word_heard(text, scored):
            self.named = True
            why = "its name, as he goes on"
        elif self.by_word and self.ear:
            return     # a Mac wake no name has cleared: a television or the room, until its name turns up
        else:
            why = "words"
        # Off the running transcripts, so a stop said meanwhile is still caught at once.
        self.ask_if_his(why, text)

    def ask_if_his(self, why: str, text: str):
        """Put these words to words_for_sunday: now, or next if it is already asking."""
        self.said_next = (why, text)
        if not self.said_busy:
            self.said_busy = True
            asyncio.get_running_loop().create_task(self.words_for_sunday())

    async def words_for_sunday(self):
        """The newest words that would clear this listen, put to the question whether they are
        his: his voice by the speaker check, or else words the directed judge reads as said to
        Sunday (a judge with no verdict in time lets them through). Until one says so nothing
        chimes, lights, turns down or shows. Words that came in while it asked are asked about
        next, so a first fragment never holds up the rest. Nothing is dropped here: what he said
        is decided when he stops."""
        qid, vad = self.qid, self.vad
        try:
            while self.said_next and self.qid == qid and self.vad is vad and not self.words_ok:
                (why, text), self.said_next = self.said_next, None
                n = len(nonspeech.words(text))
                if n <= self.said_upto:
                    continue
                self.said_upto = n
                spk = await speaker_score(self.wake_audio())
                score = spk.get("score")
                if score is not None and score >= SPEAKER_HIS:
                    ok, by = True, f"his voice, {score:.2f}"
                elif n < 2:
                    continue        # one word is too little for the judge, which calls it overheard
                else:
                    verdict = await directed(text)
                    ok, by = verdict is not False, "said to Sunday" if verdict else "no verdict in time"
                if self.qid != qid or self.vad is not vad or self.words_ok:
                    return
                if ok:
                    self.judged = by == "said to Sunday"
                    await self.heard_words(f"{why}; {by}")
                    return
                log.info("words on %s read as overheard (voice %s): nothing shows yet: %s", self.device,
                         "unscored" if score is None else f"{score:.2f}", text[:80])
        finally:
            if self.qid == qid and self.vad is vad:
                self.said_busy = False      # otherwise the next listen has begun, and is its own

    async def heard_words(self, why: str):
        """Words, not a sound: whatever was talking turns down for him now, and once the voice
        is his too (when that is checked) the device chimes and lights and the overlay shows."""
        if self.words_ok:
            return
        self.words_ok = True
        log.info("words on %s (%s), %.0f ms after the mic opened", self.device, why,
                 (time.monotonic() - self.listen_started) * 1000)
        await self.duck_others()
        if not self.hold or self.spk_ok:
            await self.show()

    async def duck_others(self):
        devs, self.duck_later = self.duck_later, []
        for dev in devs:
            await dev.send(type="duck", on=True)

    async def show(self):
        await self.send(type="speech")
        if self.show_pending and not self.unmute_only:
            broadcast(type="listening", id=self.qid, device=self.device, ear=self.ear, followup=self.followup)
        self.show_pending = False

    def wake_audio(self) -> np.ndarray:
        """The wake word, if the device sent it, and everything heard since."""
        parts = ([self.preroll] if self.preroll is not None else []) + \
            ([np.concatenate(self.heard_audio)[MIC_SETTLE:]] if self.heard_audio else [])
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def saying_now(self) -> list:
        """The answers and announcements this device is saying or still has to finish."""
        return [t for t in self.turns if not t.done() and (getattr(t, "talking", False) or getattr(t, "voiced", False))] \
            + [t for t in self.announcing if not t.done()]

    def is_talking(self) -> bool:
        return bool(self.saying_now()) or self.voice.locked() or self.play_until > time.monotonic() \
            or bool(self.sounding)

    def barge_context(self, devices: list) -> dict:
        tasks = [(d, t) for d in devices for t in d.saying_now()]
        utts = [getattr(t, "utt", None) or {} for _, t in tasks]
        # All sent and still playing on the device: what it is saying is its last utterance.
        utts += [d.last_utt for d in devices if d.last_utt and d.play_until > time.monotonic()
                 and not any(x is d for x, _ in tasks)]
        return {"at": time.time(), "devices": devices, "tasks": tasks, "early": "",
                "question": " / ".join(u.get("question", "") for u in utts if u.get("question")),
                "saying": " ".join((u.get("intended") or " ".join(u.get("sentences", []))) for u in utts).strip(),
                "talking": [{"qid": getattr(t, "qid", None), "device": d.device, "source": (getattr(t, "utt", None)
                             or {}).get("source", "")} for d, t in tasks]}

    async def stop_everything(self, why: str):
        """He said stop: every answer still being written or said and every announcement
        is cancelled on every device, not only what looks like it is playing, so nothing
        more of it is sent after the stop."""
        me = asyncio.current_task()
        everyone = list(dict.fromkeys([self] + list(cubes)))
        cut = [(d, t) for d in everyone for t in list(d.turns) + list(d.announcing) if t is not me and not t.done()]
        log.info("%s: silencing %s; cancelling %d answer(s) and announcement(s)", why,
                 ", ".join(d.device for d in everyone), len(cut))
        await self.silence(everyone, "stop", cut)

    async def silence(self, devices: list, cause: str, tasks=()):
        """Stop at once: every answer and announcement speaking on these devices."""
        for _, t in tasks:
            if not t.done():
                t.cut_cause = cause
                t.cancel()
        for dev in devices:
            dev.cut_off(cause)
            await dev.send(type="stop_audio")
            dev.play_until = time.monotonic()

    async def release_barge(self, barge: dict, verdict: dict, heard: str, qid) -> str:
        """Act on a barge-in decision and log it: "stop" silences, "redirect" cuts what was
        being said so his correction is answered, "continue" puts the volume back."""
        if self.barge is barge:
            self.barge = None
        devices = list(dict.fromkeys(barge["devices"] + [self]))
        alive = [(d, t) for d, t in barge["tasks"] if not t.done()]
        decision = verdict["decision"]
        speech_log({"kind": "barge", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                    "decision": decision, "reason": verdict.get("reason", ""), "by": verdict.get("by", ""),
                    "ms": verdict.get("ms", 0), "echo_words": verdict.get("echo", 0), "talking": barge["talking"], "question": barge["question"],
                    "saying": barge["saying"][-300:], "still_talking": [d.device for d in devices if d.is_talking()],
                    "after_s": round(time.time() - barge["at"], 2), "wake": not barge.get("over")})
        log.info("barge-in %s (%s, by %s in %d ms): %s", decision.upper(), verdict.get("reason", ""),
                 verdict.get("by", ""), verdict.get("ms", 0), heard or "(nothing)")
        if decision == "stop":
            await self.silence(devices, "stop", alive)
            return decision
        if decision == "redirect":
            for _, t in alive:
                t.cut_cause = "redirect"
                t.cancel()
            for dev in devices:
                if any(d is dev for d, _ in alive) or (dev.play_until > time.monotonic() and not dev.voice.locked()):
                    await dev.send(type="stop_audio")
                    dev.play_until = time.monotonic()
            if alive:
                # Let what was cut record itself, so the correction is answered knowing it.
                await asyncio.wait([t for _, t in alive], timeout=3)
        for dev in devices:
            await dev.send(type="duck", on=False)
        return decision

    def cut_off(self, cause: str) -> list:
        """Stop every answer that is speaking and every announcement, recording why."""
        talking = [t for t in self.turns if getattr(t, "talking", False)] + list(self.announcing)
        for t in talking:
            t.cut_cause = cause
            t.cancel()
        return talking

    # ---- the open ear: what he says while it talks, with no wake word

    def over_open(self, on: bool):
        """The device started or stopped streaming its mic while it talks."""
        if on:
            if self.over_vad is None and OVER:
                self.over_vad = new_vad(OVER_END_SILENCE, OVER_MAX)
                self.over_base = self.over_n
                self.over_stats = {"from": self.over_n, "speech": 0, "own": 0, "read": 0, "partials": 0}
            return
        if self.over_vad is None:
            return
        self.over_vad = None
        if self.over_seg is not None:
            # It stopped talking while he was still speaking: decide on what was heard.
            self.over_end(self.over_seg, self.over_n)
        st = self.over_stats
        if self.over_n > st["from"]:
            keep_over(self.over_clip(st["from"], self.over_n), self.device, "", "last-ear")
        log.info("open ear on %s: %.1f s heard, %d stretch(es) of speech, %d only its own voice, %d read again "
                 "(%d running transcripts)", self.device, (self.over_n - st["from"]) / RATE, st["speech"], st["own"],
                 st["read"], st["partials"])

    def over_clip(self, a: int, b: int) -> np.ndarray:
        parts = [x[max(a - start, 0):b - start] for start, x in self.over_audio if start + len(x) > a and start < b]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def on_over_audio(self, pcm: bytes):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.over_audio.append((self.over_n, samples))
        self.over_n += len(samples)
        while self.over_audio and self.over_n - self.over_audio[0][0] > OVER_KEEP * RATE:
            self.over_audio.popleft()
        self.over_levels.append(10 * np.log10(float(np.square(samples).mean()) + 1e-12))
        vad = self.over_vad
        vad.accept_waveform(np.clip(samples * OVER_VAD_GAIN, -1.0, 1.0))
        seg = self.over_seg
        if seg is None and vad.is_speech_detected():
            seg = self.over_begin(self.over_base + vad.current_segment.start)
        ended = None
        while not vad.empty():
            ended = self.over_base + vad.front.start + len(vad.front.samples)
            vad.pop()
        if seg is None:
            return
        if ended is None and self.over_loud(seg):
            seg["loud_at"] = time.monotonic()
            asyncio.get_running_loop().create_task(self.over_duck(seg))
        elif seg["ducked"] and not seg["stopped"] and (seg["loud_at"] or seg["cue_at"]) and time.monotonic() - \
                max(seg["loud_at"], seg["cue_at"]) > (OVER_CUE_HOLD if seg["cue_at"] else OVER_LOUD_HOLD):
            # Turned down for a loud stretch that holds nothing for Sunday, or for its name a
            # while ago: back up now, without waiting for its own voice to let the stretch end.
            asyncio.get_running_loop().create_task(self.over_release(seg))
        if ended is not None:
            self.over_end(seg, ended)
        elif self.over_n - seg["from"] > OVER_MAX * RATE:
            # Sound that never pauses holds the detector open: decide on what is there and start afresh.
            self.over_vad = new_vad(OVER_END_SILENCE, OVER_MAX)
            self.over_base = self.over_n
            self.over_end(seg, self.over_n)
        elif not seg["ignore"] and not seg["dropped"] and not seg["busy"] and not seg["stopped"] \
                and not kokoro["busy"] and time.monotonic() - seg["last_partial"] > OVER_PARTIAL_EVERY:
            seg["busy"] = True
            seg["last_partial"] = time.monotonic()
            tail = max(seg["from"], self.over_n - int(OVER_TAIL * RATE))
            if tail > seg["covered"]:
                seg["gap"] = True            # some of it was never read as it went
            seg["covered"] = self.over_n
            self.over_stats["partials"] += 1
            asyncio.get_running_loop().create_task(self.over_partial(
                seg, self.over_clip(tail, self.over_n), time.monotonic() - (self.over_n - tail) / RATE, tail))

    def over_loud(self, seg: dict) -> bool:
        """The last 0.3 s is plainly louder than its own residual voice over the 10 s before."""
        if seg["ignore"] or seg["dropped"] or seg["gated"] or seg["ducked"] or seg["loud_at"] or seg["stopped"]:
            return False
        lv = list(self.over_levels)
        if len(lv) < 125:
            return False
        recent = 10 * np.log10(np.mean(np.power(10, np.array(lv[-15:]) / 10)))
        return recent > np.percentile(lv[:-25], 90) + OVER_DUCK_DB

    def over_begin(self, start: int) -> dict:
        """Speech over its own voice. Nothing is cut or answered on the strength of it; the
        talking devices are turned down only once his words show it is him."""
        now = time.monotonic()
        frm = max(start - int(OVER_PAD * RATE), self.over_audio[0][0] if self.over_audio else 0)
        talking = [d for d in dict.fromkeys([self] + list(cubes)) if d.is_talking()]
        ignore = "muted" if muted else "nothing is talking" if not talking else ""
        # Only a plain stop counts while a computer is playing sound, which can say anything,
        # or while another device has its mic open on a wake, which hears the same words and
        # answers them itself. A stop is the same whoever acts on it; anything else would be twice.
        stop_only = (OVER_AUDIO_GATE if AUDIO_GATE and computer_playing() else
                     "another device is listening" if any(c.vad is not None for c in cubes) else "")
        seg = {"from": frm, "t0": now - (self.over_n - frm) / RATE, "t1": now, "qid": new_qid(), "ignore": ignore,
               "ctx": {**self.barge_context(talking), "over": True} if not ignore else None,
               "gated": stop_only, "ducked": [], "stopped": "", "busy": False,
               "last_partial": now - OVER_PARTIAL_EVERY / 2, "ended_at": None, "dropped": "",
               "covered": frm, "gap": False, "his_seen": False, "loud_at": 0.0, "cue_at": 0.0,
               "quiet": 0, "quiet_from": 0, "his_from": None}
        self.over_seg = seg
        over_live[id(seg)] = seg
        if not ignore:
            self.over_stats["speech"] += 1
        return seg

    def over_mine(self, seg: dict, spans: list, at: float) -> tuple:
        """(the sentences of what the open ear heard that hold words of his, those words, and
        the run of his words that ends each of those sentences).
        spans are the recognizer's sentences with their seconds into audio that began at
        time at. In each, runs of Sunday's own words are taken out first, then the sentence
        is dropped if every word left loosely matches what it was playing around that
        moment: its residual voice, its own "stop" or "Sunday" coming back through the mic,
        written down a little wrong."""
        ctx = seg["ctx"]
        kept, his, tails = [], [], []
        for sentence, a, b in spans:
            stripped, _ = bargein.strip_echo(sentence, ctx["saying"])
            around = said_around(ctx["devices"], at + a - OVER_WINDOW[0], at + b + OVER_WINDOW[1])
            words = bargein.his_words(stripped, around)
            if words:
                kept.append(" ".join(stripped.split()))
                his += words
                tails.append(bargein.his_tail(stripped, around))
        return " ".join(kept), his, [t for t in tails if t]

    async def over_duck(self, seg: dict):
        if seg["ducked"] or seg["dropped"]:
            return
        seg["ducked"] = [d for d in seg["ctx"]["devices"] if d.is_talking()]
        for d in seg["ducked"]:
            await d.send(type="duck", on=True, why="heard him over its speech")

    async def over_release(self, seg: dict):
        """Full volume again, wherever this segment turned it down and nothing else holds it down."""
        ducked, seg["ducked"] = seg["ducked"], []
        for d in ducked:
            if any(c.barge and not c.barge.get("unducked") and d in c.barge["devices"] for c in cubes) or \
                    any(c.over_seg is not None and c.over_seg is not seg and d in c.over_seg["ducked"] for c in cubes):
                continue
            await d.send(type="duck", on=False)

    async def over_partial(self, seg: dict, audio: np.ndarray, at: float, tail: int):
        """A running transcript of the last few seconds while he talks over it: stop at once
        on a plain stop, turn it down once he says its name, and decide what he said as
        soon as he has finished, rather than when its own voice lets the detector end it."""
        try:
            text, spans = await asyncio.get_running_loop().run_in_executor(None, transcribe_timed, audio)
            if seg["dropped"] or seg["stopped"] or seg["ended_at"]:
                return
            mine, his, tails = self.over_mine(seg, spans, at)
            if not his:
                if seg["his_seen"] and self.over_seg is seg:
                    seg["quiet"] += 1
                    if seg["quiet"] == 1:
                        seg["quiet_from"] = tail
                    elif self.over_vad is not None:
                        # Two running transcripts in a row with nothing of his: he has finished.
                        self.over_vad = new_vad(OVER_END_SILENCE, OVER_MAX)
                        self.over_base = self.over_n
                        self.over_end(seg, min(seg["quiet_from"] + int(0.6 * RATE), self.over_n))
                return
            seg["his_seen"] = True
            seg["quiet"] = 0
            if seg["his_from"] is None:
                seg["his_from"] = tail
            plain = any(bargein.PLAIN_STOP.match(x) for x in bargein.sentences(mine) + tails)
            if plain or (seg["gated"] in ("", OVER_AUDIO_GATE) and said_to_sunday(mine)):
                seg["cue_at"] = time.monotonic()
                await self.over_duck(seg)    # it could be for Sunday: turned down while it is decided
            if plain and over_claim(seg, self):
                seg["stopped"] = mine
                log.info("heard over its speech: stopped at once on %r, %.2f s into it", mine,
                         time.monotonic() - seg["t0"])
                await self.stop_everything("stop (heard over its speech)")
                seg["ducked"] = []
        finally:
            seg["busy"] = False

    def over_end(self, seg: dict, end: int):
        if self.over_seg is seg:
            self.over_seg = None
        if seg["ignore"] or seg["dropped"] or end - seg["from"] < int(0.3 * RATE):
            over_live.pop(id(seg), None)
            if seg["ducked"]:
                asyncio.get_running_loop().create_task(self.over_release(seg))
            return
        now = time.monotonic()
        seg["t1"] = now - (self.over_n - end) / RATE
        seg["ended_at"] = now
        if not (seg["his_seen"] or seg["gap"] or seg["busy"]) and seg["covered"] >= end:
            # Read all the way through as it went, and every word of it was its own: no
            # need to read it again. Most of what the open ear hears is this.
            self.over_stats["own"] += 1
            over_live.pop(id(seg), None)
            if seg["ducked"]:
                asyncio.get_running_loop().create_task(self.over_release(seg))
            return
        # Read from a little before his words were first seen, when every bit of it was read
        # as it went: the recognizer folds a few words of his into seconds of its own voice
        # read with them, and writes them as more of its sentence.
        frm = seg["from"] if seg["his_from"] is None or seg["gap"] else \
            max(seg["from"], seg["his_from"] - int(1.2 * RATE))
        audio = self.over_clip(frm, min(end + int(0.2 * RATE), self.over_n))
        self.over_stats["read"] += 1
        asyncio.get_running_loop().create_task(self.over_decide(seg, audio, seg["t0"] + (frm - seg["from"]) / RATE))

    async def over_decide(self, seg: dict, audio: np.ndarray, at: float):
        """He said something while it talked, with no wake word. Its own voice and anything
        not for Sunday is dropped unanswered and changes nothing; an obvious stop stops
        everything; an explicit correction cuts it to answer; words that start with its
        name are taken as the wake-word barge-in they are."""
        ctx, qid = seg["ctx"], seg["qid"]
        spk_task = asyncio.get_running_loop().create_task(speaker_score(audio))
        try:
            try:
                heard, spans = await asyncio.get_running_loop().run_in_executor(None, transcribe_timed, audio)
            except Exception:
                log.exception("open ear: transcription failed")
                await self.over_release(seg)
                return
            t_text = time.monotonic()
            spk = await spk_task
            enforce = SPEAKER and voiceprint.enforcing
            action, spk_why = speaker_action(spk, barge=True)
            if not enforce and action != "pass":
                spk_why = f"shadow, would {'ignore' if action == 'reject' else 'judge the words'}: {spk_why}"

            def spk_done(done: str, text: str = "") -> dict:
                return speaker_log("open ear", self.device, qid, spk, action, spk_why, done, text or heard,
                                   {"kind": "open ear", "waited_ms": int(max(0.0, time.monotonic() - t_text) * 1000),
                                    "computer_audio": computer_playing()})

            keep_over(audio, self.device, heard)
            if seg["dropped"]:
                return
            mine, his, tails = self.over_mine(seg, spans, at)
            rec = {"kind": "overheard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                   "talking": ctx["talking"], "seconds": round(len(audio) / RATE, 2),
                   "to_text_ms": int((t_text - seg["ended_at"]) * 1000)}
            if seg["stopped"]:
                speech_log({**rec, "command": "stop", "stopped_on": seg["stopped"],
                            "speaker": spk_done("stopped: a stop over its speech acts whoever says it")})
                self.over_log(seg, mine or heard, {"decision": "stop", "by": "partial", "ms": 0,
                                                   "reason": f"stopped at once on {seg['stopped']!r}"})
                return
            if not his:
                await self.over_release(seg)
                speech_log({**rec, "discarded": "its own voice" if heard.strip() else "nothing",
                            "speaker": spk_done("its own voice")})
                log.info("heard over its speech, only its own voice: %s", heard or "(nothing)")
                return
            no_words = nonspeech.junk(mine)
            if no_words and not any(bargein.STOP.match(x) for x in tails) \
                    and not MUTE_SAID.match(" ".join(mine.split())):
                # A cough or a clatter over its speech, written down as a filler or a made-up word.
                await self.over_release(seg)
                speech_log({**rec, "discarded": "no words: " + no_words, "speaker": spk_done("no words: " + no_words, mine)})
                log.info("heard over its speech, no words (%s): %r", no_words, mine)
                return
            if not over_claim(seg, self):
                await self.over_release(seg)
                speech_log({**rec, "discarded": "heard on another device too", "speaker": spk_done("another device has it")})
                return
            if not any(bargein.STOP.match(x) for x in tails):
                # Its own voice, written down badly enough that a few words looked like his:
                # "7 o'clock one 28 hour we loans 47" while it said "seven o clock ... twenty
                # eight ... three forty seven". Mostly its own words, so dropped whole.
                own = bargein.own_voice(mine, " ".join(x for x in (said_lately(
                    seg["t0"] - ECHO_LAG), ctx["saying"]) if x))
                if own:
                    await self.over_release(seg)
                    self.over_stats["own"] += 1
                    speech_log({**rec, "discarded": "its own voice", "reason": own["why"],
                                "speaker": spk_done("its own voice")})
                    log.info("heard over its speech, only its own voice (%s): %s", own["why"], mine)
                    return
            plain_stop = any(bargein.STOP.match(x) for x in tails + bargein.sentences(mine)) \
                or bool(MUTE_SAID.match(" ".join(mine.split())))
            if enforce and action == "reject" and not plain_stop:
                # Not his voice: a TV, a phone, the other computer. It changes nothing.
                await self.over_release(seg)
                speech_log({**rec, "discarded": "not his voice", "speaker": spk_done("ignored", mine)})
                return
            # Its name is enough over a computer's sound; a wake, here or on another device,
            # takes it itself.
            named = said_to_sunday(mine) if seg["gated"] in ("", OVER_AUDIO_GATE) else ""
            if named:
                # He said its name: the barge-in a wake word starts, which may answer him.
                log.info("heard over its speech, said to Sunday: %s", named)
                await self.over_duck(seg)
                seg["ducked"] = []           # the barge-in owns the volume now
                self.t_end = seg["ended_at"]
                ctx["spk"] = spk
                self.over_turn(qid, ctx, named, seg["t0"])
                return
            if any(bargein.STOP.match(x) for x in tails):
                # A stop run into the sentence of its own voice around it.
                verdict = {"decision": "stop", "by": "regex", "ms": 0, "reason": "explicit stop", "text": mine, "echo": 0}
            elif seg["gated"]:
                stop = any(bargein.STOP.match(x) for x in bargein.sentences(mine))
                verdict = {"decision": "stop" if stop else "continue", "by": "regex" if stop else "code", "ms": 0,
                           "reason": "explicit stop" if stop else f"{seg['gated']}, and not plainly a stop",
                           "text": mine, "echo": 0}
            else:
                if bargein.may_want_sunday(mine):
                    await self.over_duck(seg)    # quieter while the judge reads it
                verdict = await bargein.decide(mine, ctx["saying"], ctx["question"], addressed=False)
            if seg["dropped"]:
                return
            decision = verdict["decision"] if verdict["by"] != "echo" else "echo"
            if decision == "redirect" and enforce and action == "borderline" and await directed(mine) is False:
                verdict = {**verdict, "decision": "continue", "by": "speaker",
                           "reason": "not plainly his voice, and not said to Sunday"}
                decision = "continue"
            if decision != "redirect":
                self.over_log(seg, mine, verdict)     # a redirect is logged where it is acted on
            if decision == "stop":
                speech_log({**rec, "command": "stop",
                            "speaker": spk_done("stopped: a stop over its speech acts whoever says it", mine)})
                await self.stop_everything("stop (heard over its speech)")
                seg["ducked"] = []
                return
            if decision == "redirect":
                # An explicit correction: cut what is being said and answer it, as a barge-in would.
                spk_done("redirect", mine)
                ctx["verdict"] = verdict
                ctx["spk"] = spk
                await self.over_duck(seg)
                seg["ducked"] = []
                self.t_end = seg["ended_at"]
                self.over_turn(qid, ctx, verdict.get("text") or mine, seg["t0"])
                return
            await self.over_release(seg)
            why = "its own voice" if decision == "echo" else "heard while talking, not addressed"
            speech_log({**rec, "discarded": why, "reason": verdict.get("reason", ""),
                        "speaker": spk_done("dropped: " + why, mine)})
            log.info("%s (%s, by %s): %s", why, verdict.get("reason", ""), verdict.get("by", ""), mine)
        finally:
            over_live.pop(id(seg), None)
            if seg["ducked"] and not seg["dropped"]:
                await self.over_release(seg)

    def over_log(self, seg: dict, heard: str, verdict: dict):
        ctx = seg["ctx"]
        speech_log({"kind": "barge", "at": time.time(), "qid": seg["qid"], "device": self.device, "text": heard,
                    "decision": verdict["decision"], "reason": verdict.get("reason", ""), "by": verdict.get("by", ""),
                    "ms": verdict.get("ms", 0), "echo_words": verdict.get("echo", 0), "talking": ctx["talking"],
                    "question": ctx["question"], "saying": ctx["saying"][-300:], "wake": False,
                    "after_s": round(time.time() - ctx["at"], 2)})
        log.info("heard over its speech: %s (%s, by %s in %d ms): %s", verdict["decision"].upper(),
                 verdict.get("reason", ""), verdict.get("by", ""), verdict.get("ms", 0), heard)

    def over_turn(self, qid: str, ctx: dict, heard: str, began: float = 0.0):
        """Hand what he said over it to the barge-in path, as if he had woken it to say it."""
        task = asyncio.create_task(self.turn(None, qid, False, computer_playing(), ctx, heard=heard,
                                             began=began or None, spk=ctx.get("spk")))
        task.qid = qid
        self.turns.add(task)
        task.add_done_callback(self.turns.discard)
        task.add_done_callback(lambda _t: end_owned(qid))

    def start_turn(self, segment):
        barge, self.barge = self.barge, None
        if self.unmute_only:
            task = asyncio.create_task(self.unmute_check(segment))
            if barge:
                asyncio.get_running_loop().create_task(self.release_barge(
                    barge, {"decision": "continue", "reason": "muted", "by": "code", "ms": 0}, "", self.qid))
        elif self.followup and time.monotonic() < self.enroll_until:
            self.enroll_until = 0.0
            task = asyncio.create_task(self.enroll_capture(segment, self.qid))
        else:
            began = self.t_end - len(segment) / RATE if segment is not None else None
            # Woken while it was talking, its own voice can be in what it heard; woken quiet,
            # only the last moment of it; a follow-up window opens once its playback is over.
            own_after = None if barge else self.listen_started - (0.0 if self.followup else OWN_TAIL)
            wake = {"preroll": self.preroll, "info": self.wake_info, "hold": self.hold, "cleared": self.spk_ok,
                    "prob": (self.wake_info or {}).get("prob"), "words": self.words_ok, "by_word": self.by_word,
                    "judged": getattr(self, "judged", False),
                    "named": getattr(self, "named", False), "lone": getattr(self, "lone", False)}
            task = asyncio.create_task(self.turn(segment, self.qid, self.followup, computer_playing(), barge,
                                                 began=began, own_after=own_after, wake=wake))
        task.qid = self.qid
        self.turns.add(task)
        task.add_done_callback(self.turns.discard)
        # However the turn ends (answered, cut off, failed, its cube gone), nothing it
        # started stays in the queue.
        qid = self.qid
        task.add_done_callback(lambda _t: end_owned(qid))

    def open_mic(self, followup: bool = False, sure: bool = True):
        self.followup = followup       # opened after a reply, without the wake word
        self.no_speech_timeout = FOLLOWUP_WINDOW if followup else NO_SPEECH_TIMEOUT
        enrolling = followup and time.monotonic() < self.enroll_until
        if enrolling:
            # His answer to the request for his voice: more time to start, and pauses between
            # sentences don't end it.
            self.no_speech_timeout = 12.0
        self.vad = new_vad(3.0, 40.0) if enrolling else new_vad()
        self.listen_started = time.monotonic()
        self.last_audio = time.monotonic()
        self.heard_speech = False
        self.audio_bytes = 0
        self.heard_audio = []
        self.received = 0          # samples since the mic opened
        self.last_partial = 0.0
        self.partial_busy = False
        self.qid = new_qid()           # this question's id, on every event about it
        self.unmute_only = muted       # muted: listen for "unmute" and nothing else
        if followup:
            self.preroll, self.wake_info = None, None
        # Enforcing, a wake is quiet and unseen until the voice is his (verified()).
        # A follow-up window has no chime to hold, but what shows of it waits the same way.
        self.hold = SPEAKER and voiceprint.enforcing and not self.unmute_only \
            and (self.wake_info or {}).get("by") != "button"
        self.spk_ok, self.spk_busy, self.spk_early, self.spk_next = False, False, None, SPEAKER_EARLY_S
        # Nothing chimes, lights, turns down or shows until words are heard: the wake word's
        # own name in it (word_check), or words as he goes on (words_in). A cough, a click or
        # a dish has neither. The button is someone at the cube, and needs no words.
        button = (self.wake_info or {}).get("by") == "button"
        self.by_word = not followup and not button
        self.words_ok, self.words_busy, self.duck_later = button, False, []
        self.said_busy, self.said_upto, self.said_next = False, 0, None   # words_for_sunday: asking now, how much it asked about, what is next
        self.judged = False            # the judge read words of this listen as said to Sunday
        self.named = False             # "Sunday" itself was heard in this listen
        self.lone, self.lone_at = False, 0.0   # a Mac wake word said alone, then a pause (lone_word)
        self.last_said = None          # the newest running transcript, (text, scored)
        self.name_busy, self.name_wait_until = False, 0.0   # name_only: deciding, and open for his words until
        self.show_pending = True
        if self.words_ok and not self.hold and not self.unmute_only:
            broadcast(type="listening", id=self.qid, device=self.device, ear=self.ear, followup=followup)
            self.show_pending = False
        asyncio.get_running_loop().create_task(self.mic_watchdog(self.vad))

    async def mic_watchdog(self, vad):
        """Close the mic even if the cube stops sending audio, so it never waits forever."""
        await asyncio.sleep(self.no_speech_timeout + 1)
        while self.vad is vad:
            stalled = time.monotonic() - self.last_audio > 1.5
            if self.heard_speech and (stalled or time.monotonic() - self.listen_started > MAX_UTTERANCE):
                # Background sound can keep the detector from ever hearing him stop, and
                # the cube stops sending when muted. Answer what was heard rather than drop it.
                log.info("ending the utterance (%s): %d bytes of audio", "audio stopped" if stalled else "time limit",
                         self.audio_bytes)
                segment = np.concatenate(self.heard_audio)[MIC_SETTLE:]
                self.vad = None
                self.t_end = time.monotonic()
                await self.send(type="stop_listening")
                self.start_turn(segment)
                return
            if not self.heard_speech:
                log.info("closing the mic: %d bytes of audio arrived, speech %s", self.audio_bytes, self.heard_speech)
                self.vad = None
                if self.barge:
                    await self.release_barge(self.barge, {"decision": "continue", "reason": "no speech heard",
                                                          "by": "code", "ms": 0}, "", self.qid)
                await self.send(type="stop_listening")
                await self.send(type="idle")
                broadcast(type="idle", id=self.qid)
                return
            await asyncio.sleep(1)

    async def unnamed_check(self, qid, vad):
        """A Mac listen its name has not cleared after UNNAMED_CLOSE_S: read the wake word and
        everything since as one stretch, which holds "Sunday, what time is it" far better than
        the wake word's second alone. The name there clears it; otherwise it was a television
        or the room, and the mic closes now instead of staying open on that talk."""
        parts = ([self.preroll] if self.preroll is not None else []) + [np.concatenate(self.heard_audio)[MIC_SETTLE:]]
        lone = self.lone
        try:
            text, scored = await asyncio.get_running_loop().run_in_executor(
                None, transcribe_scored, np.concatenate(parts), False)
            if lone:
                # Its name said alone: what he said after the pause is the question.
                after, after_scored = await asyncio.get_running_loop().run_in_executor(
                    None, transcribe_scored, np.concatenate(self.heard_audio)[int((LONE_TO + self.lead()) * RATE):],
                    False)
        except Exception:
            log.exception("name check over the first seconds failed; leaving the mic open")
            return
        if self.qid != qid or self.vad is not vad or self.words_ok:
            return
        if lone and words_past_name(after, after_scored):
            self.ask_if_his("words after its name said alone", after)
            return
        if self.named:
            return      # its name turned up meanwhile (name_only): the mic is open for his words
        if nonspeech.wake_word_heard(text, scored) and not nonspeech.wake_word_heard(said_lately(time.monotonic() - 3.0)):
            self.named = True
            if words_past_name(text, scored):
                log.info("its name in the first seconds on %s: %s", self.device, text[:80])
                self.ask_if_his("its name, with what followed", text)
            else:
                log.info("its name in the first seconds on %s, and no words yet: the mic stays open for them",
                         self.device)
            return
        log.info("closing the mic on %s: no name in its first %.0f s (%r), so not said to Sunday",
                 self.device, UNNAMED_CLOSE_S, text[:80])
        try:
            # Kept for reading back what a closed Mac wake really held: the last 30 of them.
            import soundfile
            keep = DATA / "closed-wakes"
            keep.mkdir(exist_ok=True)
            soundfile.write(keep / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.device}.wav", np.concatenate(parts), RATE)
            for old in sorted(keep.glob("*.wav"))[:-30]:
                old.unlink()
        except Exception:
            pass
        speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": text,
                    "discarded": "no words: no name in the first seconds"})
        self.vad = None
        if self.barge:
            await self.release_barge(self.barge, {"decision": "continue", "reason": "no name heard",
                                                  "by": "code", "ms": 0}, "", qid)
        await self.send(type="stop_listening")
        await self.send(type="idle")
        broadcast(type="idle", id=qid)

    def lead(self) -> float:
        """Seconds of what the Mac heard before its wake word fired, at the head of the audio it
        sends: "Sunday, what time is it?" in one breath is at "is it" by the time the wake word
        fires, so the Mac sends the question from before that, and the mic-opened times here
        start that much later."""
        try:
            return max(0.0, min(2.0, float((self.wake_info or {}).get("lead") or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    async def lone_word(self):
        """A Mac wake its name check could not read. "Sunday" said at the Mac and then a pause
        is him, however the second before it transcribed; a television or the room goes on
        talking. So what he says after the pause counts as said to Sunday, as on the cube. It
        chimes only once those words are in (words_in), never for the name alone."""
        try:
            level = float((self.wake_info or {}).get("level"))
        except (TypeError, ValueError):
            return
        if level < LONE_LOUD:
            return
        lead = self.lead()
        after = np.concatenate(self.heard_audio)[int((LONE_FROM + lead) * RATE):int((LONE_TO + lead) * RATE)]
        n = len(after) // 800 * 800
        if not n:
            return
        loudest = 20 * np.log10(max(float(np.sqrt((after[:n].reshape(-1, 800) ** 2).mean(axis=1)).max()), 1e-9))
        if loudest > level - LONE_DROP:
            return
        self.lone, self.lone_at = True, time.monotonic()
        log.info("its name said alone on %s (%.0f dBFS, then %.0f dBFS): nothing chimes until his words are in",
                 self.device, level, loudest)

    async def end_utterance(self):
        """He has stopped: the utterance is everything since the mic opened. He starts talking
        right after the wake word, and the detector notices speech too late to mark where it
        began, so it is only trusted for "he has stopped"."""
        segment = np.concatenate(self.heard_audio)[MIC_SETTLE:]
        self.vad = None
        self.t_end = time.monotonic()
        await self.send(type="stop_listening")
        self.start_turn(segment)

    def may_wait_for_words(self) -> bool:
        """He paused in a Mac listen that nothing has cleared and that has its name in it: it
        may hold its name and nothing else yet (name_only)."""
        if not (self.ear and self.by_word and not self.words_ok and not self.unmute_only and not self.barge):
            return False
        said = self.last_said
        return self.named or self.lone or bool(said and nonspeech.wake_word_heard(*said))

    async def name_only(self, qid, vad):
        """He paused in a Mac listen its name opened. With his words after its name in it, it
        ends here as any utterance does. With its name and nothing after it ("Sunday." and a
        wait for the chime, which waits for his words, or "Sunday" said again), the mic stays
        open NAME_WAIT_S for what he wants, and nothing chimes, lights or shows until it is in
        (words_in). No name anywhere in it, the wake word and everything since read as one
        stretch, and it was a sound: it ends, and is dropped as one."""
        loop = asyncio.get_running_loop()
        audio = np.concatenate(self.heard_audio)[MIC_SETTLE:]
        text, scored, name = "", [], False
        try:
            text, scored = await loop.run_in_executor(None, transcribe_scored, audio, False)
            name = nonspeech.wake_word_heard(text, scored)
            if not name and not self.named and self.preroll is not None and not words_past_name(text, scored):
                name = nonspeech.wake_word_heard(*await loop.run_in_executor(
                    None, transcribe_scored, np.concatenate([self.preroll, audio]), False))
        except Exception:
            log.exception("reading a pause after its name failed; ending the utterance")
        if self.qid != qid or self.vad is not vad:
            return
        self.name_busy = False
        if name and not nonspeech.wake_word_heard(said_lately(time.monotonic() - 3.0)):
            self.named = True
        if self.words_ok or not self.named or words_past_name(text, scored):
            await self.end_utterance()
            return
        self.name_wait_until = time.monotonic() + NAME_WAIT_S
        log.info("its name and nothing after it yet on %s (%r): the mic stays open %.0f s for what he wants, "
                 "and nothing chimes until it is in", self.device, text[:60], NAME_WAIT_S)

    async def on_audio(self, pcm: bytes):
        self.audio_bytes += len(pcm)
        self.last_audio = time.monotonic()
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.heard_audio.append(samples)
        self.received += len(samples)
        self.vad.accept_waveform(samples)
        if self.vad.is_speech_detected() and not self.heard_speech:
            # The detector calls a cough speech too: what clears the wake is the words in the
            # running transcripts (words_in), never this.
            self.heard_speech = True
        if self.hold and not self.spk_ok and self.heard_speech and not self.spk_busy \
                and self.received - MIC_SETTLE >= self.spk_next * RATE:
            # Held: score the wake word and what he has said so far, again a little later if
            # the first try was not plainly him.
            self.spk_next += SPEAKER_EARLY_S
            asyncio.get_running_loop().create_task(self.early_check("so far"))
        if self.heard_speech and (viewers or self.barge or not self.words_ok) and not self.unmute_only \
                and not self.partial_busy and time.monotonic() - self.last_partial > PARTIAL_EVERY:
            self.partial_busy = True
            self.last_partial = time.monotonic()
            asyncio.get_running_loop().create_task(self.partial(np.concatenate(self.heard_audio)[MIC_SETTLE:], self.qid))
        if not self.vad.empty() and not self.name_busy:
            if self.may_wait_for_words():
                # A pause after its name, perhaps with nothing else said: read it before the mic
                # closes on him (name_only), and keep listening meanwhile.
                self.name_busy = True
                while not self.vad.empty():
                    self.vad.pop()
                asyncio.get_running_loop().create_task(self.name_only(self.qid, self.vad))
            else:
                await self.end_utterance()
                return
        if self.name_wait_until and not self.name_busy and time.monotonic() > self.name_wait_until \
                and not self.vad.is_speech_detected():
            log.info("nothing after its name on %s in %.0f s: the mic closes", self.device, NAME_WAIT_S)
            await self.end_utterance()
            return
        if self.ear and self.by_word and not self.words_ok and not self.named and not self.unmute_only \
                and getattr(self, "lone_qid", None) != self.qid and self.received >= int((LONE_TO + self.lead()) * RATE):
            self.lone_qid = self.qid
            await self.lone_word()
        if self.ear and self.by_word and not self.words_ok and not self.named and not self.unmute_only \
                and not getattr(self, "unnamed_qid", None) == self.qid \
                and time.monotonic() - (self.lone_at or self.listen_started) > UNNAMED_CLOSE_S:
            self.unnamed_qid = self.qid
            asyncio.get_running_loop().create_task(self.unnamed_check(self.qid, self.vad))
        if not self.heard_speech and time.monotonic() - self.listen_started > self.no_speech_timeout:
            self.vad = None
            await self.send(type="stop_listening")
            audio = np.concatenate(self.heard_audio)[MIC_SETTLE:]
            frames = audio[: len(audio) // 800 * 800].reshape(-1, 800) if len(audio) >= 800 else audio.reshape(1, -1)
            peak = 20 * np.log10(max(float(np.sqrt((frames ** 2).mean(axis=1)).max()), 1e-9))
            if peak > NO_SPEECH_RETRY_DB:
                # The detector can miss him right after a reply, while the echo canceller
                # settles. If there was real sound, let speech recognition decide.
                log.info("wake with no speech detected, peak %.0f dBFS: transcribing anyway", peak)
                self.t_end = time.monotonic()
                self.start_turn(audio)
                return
            if self.barge:
                await self.release_barge(self.barge, {"decision": "continue", "reason": "no speech heard",
                                                      "by": "code", "ms": 0}, "", self.qid)
            await self.send(type="idle")
            broadcast(type="idle", id=self.qid)
            log.info("wake with no speech, peak %.0f dBFS", peak)
            if self.followup and self.enroll_until:
                self.enroll_until = 0.0
                log.info("voiceprint: no answer to the request for his voice")

    async def partial(self, audio: np.ndarray, qid: str):
        """A running transcript while he is still talking: what clears a wake that has not
        been cleared yet (words_in), and the popup's."""
        try:
            text, scored = await asyncio.get_running_loop().run_in_executor(None, transcribe_scored, audio, False)
            if text and self.vad is not None and self.qid == qid:
                self.last_said = (text, scored)
                if not self.words_ok:
                    await self.words_in(text, scored)
                if self.words_ok and not (self.hold and not self.spk_ok):
                    broadcast(type="partial", id=qid, text=text)   # held: nothing shows until it is his
                barge = self.barge
                if barge and self not in barge["devices"] and not barge["early"] and self.words_ok:
                    await self.barge_volume(barge, text)
                if barge and not barge["early"] and bargein.EARLY_STOP.match(" ".join(text.split())):
                    # "Stop", "hold on", "never mind" stay instant: cut now, while he is still
                    # talking, and let the whole transcript decide whether anything follows.
                    barge["early"] = text
                    log.info("barge-in: stopped at once on %r", text)
                    await self.silence(list(dict.fromkeys(barge["devices"] + [self])), "stop", barge["tasks"])
        finally:
            self.partial_busy = False

    async def barge_volume(self, barge: dict, text: str):
        """This device woke while another one talks, so its mic hears that one's voice
        for as long as it goes on, and his turn is decided only once it stops. The voice
        it is listening over goes back to full volume while all it has heard is that
        voice, and down again once his words show up."""
        rest, _ = bargein.strip_echo(text, barge["saying"])
        his = bargein.his_words(rest, barge["saying"])
        if not his and time.time() - barge["at"] > 1.5 and not barge.get("unducked"):
            barge["unducked"] = True
            log.info("barge-in on %s: only %s's voice so far, full volume while it listens", self.device,
                     ", ".join(d.device for d in barge["devices"]))
            for d in barge["devices"]:
                if not any(c.over_seg is not None and d in c.over_seg["ducked"] for c in cubes):
                    await d.send(type="duck", on=False)
        elif his and barge.get("unducked"):
            barge["unducked"] = False
            log.info("barge-in on %s: his words over it (%s), ducked while they are decided", self.device,
                     " ".join(his))
            for d in barge["devices"]:
                if d.is_talking():
                    await d.send(type="duck", on=True)

    async def enroll_capture(self, segment: np.ndarray, qid: str):
        """His answer to the one spoken request for his voice: kept as samples of it, never
        answered as a question. Stretches unlike the rest (a TV under him) are left out."""
        loop = asyncio.get_running_loop()
        try:
            heard = await loop.run_in_executor(None, transcribe, segment, False)
            kept = []
            for c in speaker_id.chunks(segment):
                emb, secs = await loop.run_in_executor(None, voiceprint.embed, c)
                if emb is not None:
                    kept.append((emb, secs))
            if len(kept) >= 3:
                e = np.stack([x for x, _ in kept])
                sims = e @ speaker_id._norm(e.mean(axis=0))
                kept = [k for k, s in zip(kept, sims) if s >= float(np.median(sims)) - 0.15]
            before = voiceprint.state.get("mode")
            for emb, secs in kept:
                voiceprint.add_sample(emb, {"device": self.device, "seconds": round(secs, 2), "text": heard}, "enroll")
            mode_changed(before)
            total = sum(s for _, s in kept)
            log.info("voiceprint: enrollment on %s, %d stretch(es), %.1f s of speech: %s", self.device, len(kept),
                     total, heard[:200])
            speech_log({"kind": "enroll", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "samples": len(kept), "speech_s": round(total, 1), "voiceprint": voiceprint.status()})
            self.archive(segment, {"qid": qid, "device": self.device, "text": heard, "kind": "enrollment",
                                   "done": f"kept {len(kept)} stretch(es) as his"})
            line = ("Thanks, I have your voice now." if total >= 6 else
                    "Thanks. I'll learn the rest of your voice as we talk." if kept else
                    "I didn't catch that, so I'll learn your voice as we talk instead.")
            await self.announce(line, new_qid(), "command")
        except Exception:
            log.exception("enrollment failed")
        finally:
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")

    async def ask_for_voice(self):
        """The one spoken request for a few sentences of his voice."""
        qid = new_qid()
        broadcast(type="announce", id=qid, text=ENROLL_ASK)
        await self.announce(ENROLL_ASK, qid, "enroll")
        self.enroll_until = time.monotonic() + 90
        await self.send(type="listen")   # the mic opens once it has finished saying it

    async def unmute_check(self, segment: np.ndarray):
        """Muted: a wake is only for "unmute". Anything else is dropped, unheard by Claude."""
        heard = await asyncio.get_running_loop().run_in_executor(None, transcribe, segment)
        if heard and UNMUTE_SAID.search(heard):
            set_muted(False, "voice")
        else:
            log.info("muted; dropped: %s", heard or "(nothing)")
        await self.send(type="idle")

    # ---- thinking and speaking

    def archive(self, audio, meta: dict):
        """Keep this wake's audio and what was decided, for setting the threshold from real wakes."""
        if SPEAKER and audio is not None and len(audio):
            asyncio.get_running_loop().run_in_executor(None, voiceprint.keep_wake, audio, meta)

    async def ignore(self, qid, heard: str, followup: bool, why: str, spk_rec: dict):
        """Not his voice: nothing is said, nothing chimes, nothing shows."""
        log.info("ignored (%s): %s", why, heard)
        speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                    "followup": followup, "discarded": why, "speaker": spk_rec})
        broadcast(type="idle", id=qid)
        if not self.turns - {asyncio.current_task()} and self.vad is None:
            await self.send(type="idle")

    async def turn(self, segment: np.ndarray, qid: str, followup: bool = False, playing: bool = False,
                   barge: dict | None = None, heard: str | None = None, began: float | None = None,
                   own_after: float | None = None, wake: dict | None = None, spk: dict | None = None):
        loop = asyncio.get_running_loop()
        asyncio.current_task().qid = qid
        enforce = SPEAKER and voiceprint.enforcing
        # Held, nothing shows until the voice is his; the same while enforcing for anything else.
        # And nothing shows for a listen no words have cleared yet, until these are found to be words.
        words = not wake or wake.get("words", True)
        quiet = (enforce and not (wake and wake.get("cleared"))) or not words
        if not quiet:
            broadcast(type="status", id=qid, text="Thinking", kind="thinking")
        # Whose voice, scored alongside the transcription: the wake word and what followed it.
        spk_audio = None
        if segment is not None:
            pre = (wake or {}).get("preroll")
            spk_audio = np.concatenate([pre, segment]) if pre is not None else segment
        spk_task = loop.create_task(speaker_score(spk_audio)) if spk is None else None
        scored = None              # how sure the recognizer was of each word (nonspeech.py)
        try:
            if heard is None:      # already transcribed when the open ear heard it
                heard, scored = await loop.run_in_executor(None, transcribe_scored, segment)
        except BaseException:
            if spk_task:
                spk_task.cancel()
            if barge:
                await self.release_barge(barge, {"decision": "continue", "reason": "transcription failed",
                                                 "by": "code", "ms": 0}, "", qid)
            raise
        t_text = time.monotonic()
        if spk_task:
            spk = await spk_task
        spk = spk or {"score": None, "verdict": "off", "speech_s": 0.0, "ms": 0, "emb": None}
        waited = max(0.0, time.monotonic() - t_text)
        kind = "open ear" if segment is None else "follow-up" if followup else \
            "wake over its speech" if barge else "wake"
        info = (wake or {}).get("info") or {}
        action, spk_why = speaker_action(spk, barge=bool(barge), playback=own_playback(info))
        if info.get("by") == "button":
            action, spk_why = "pass", "the button: someone at the cube"
        plain = " ".join((heard or "").split())
        always = bool(MUTE_SAID.match(plain) or STOP_SAID.match(plain) or bargein.STOP.match(plain)
                      or (barge and barge.get("early")))
        # "Close that", "got it", "thanks": with a canvas on his screen, words that take it down.
        dismiss = bool(showing) and canvases.is_dismissal(plain)
        spk_extra = {"kind": kind, "waited_ms": int(waited * 1000), "prob": info.get("prob"),
                     "echo": info.get("echo"), "computer_audio": playing or computer_playing(),
                     "early": (self.spk_early or {}).get("score") if wake else None}

        def spk_done(done: str) -> dict:
            rec = speaker_log(kind, self.device, qid, spk, action, spk_why, done, heard or "", spk_extra)
            self.archive(spk_audio, {"qid": qid, "device": self.device, "text": heard, **rec,
                                     "followup": followup, "barge": bool(barge)})
            return rec

        # No words, no answer and no sound: a cough, a click or a dish written down as nothing,
        # a filler, an annotation or a word the recognizer made up. A wake word no words have
        # cleared needs its own name in what it heard too, or what woke it was a sound. A
        # plain stop or mute always acts.
        no_words = "" if always else nonspeech.junk(heard or "", scored)
        if no_words == "a phrase the recognizer makes up from noise" and dismiss and scored \
                and all(c >= nonspeech.SURE for _, c in scored):
            # A plain "thanks" is what the recognizer writes for noise, so on its own it is dropped.
            # With a canvas up and every word of it heard clearly, it is him closing it.
            no_words = ""
        if not no_words and not always and not dismiss and not words and (wake or {}).get("by_word") \
                and not words_past_name(heard or "", scored):
            no_words = "its name and nothing after it"     # its name alone asks for nothing
        # The cube's second before its wake rarely holds its name even when he said it, so on
        # the cube real words are enough here (and are weighed below, if nothing cleared them).
        # So on the Mac after its name said alone (lone_word).
        if not no_words and not always and not words and (wake or {}).get("by_word") and self.ear \
                and not (wake or {}).get("lone") and not nonspeech.wake_word_heard(heard, scored):
            pre = wake.get("preroll")
            whole = await loop.run_in_executor(None, transcribe_scored, np.concatenate([pre, segment]), False) \
                if pre is not None and segment is not None else ("", [])
            if not nonspeech.wake_word_heard(*whole):
                no_words = "woken by a sound: its name is not in what it heard"
        if no_words:
            log.info("dropped, no words (%s): %r", no_words, heard or "")
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard or "",
                        "followup": followup, "discarded": "no words: " + no_words,
                        "speaker": spk_done("no words: " + no_words)})
            if barge:
                await self.release_barge(barge, {"decision": "continue", "reason": "no words: " + no_words,
                                                 "by": "code", "ms": 0}, heard or "", qid)
            broadcast(type="idle", id=qid)
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return

        if not enforce and action != "pass":
            spk_why = f"shadow, would {'ignore' if action == 'reject' else 'judge the words'}: {spk_why}"
        # What any device was playing while he spoke: a transcript that is mostly that is its
        # own voice coming back through a mic, and is dropped whole, never answered.
        recent = said_lately((began if began is not None else self.t_end - 10) - ECHO_LAG, self, own_after)
        redirect_note = ""
        if barge:
            if enforce and action == "reject" and not always:
                # Not his voice over Sunday's: it keeps talking at full volume, and nothing is answered.
                await self.release_barge(barge, {"decision": "continue", "reason": "not his voice: " + spk_why,
                                                 "by": "speaker", "ms": spk.get("ms", 0)}, heard, qid)
                await self.ignore(qid, heard, followup, "not his voice", spk_done("ignored"))
                return
            # He talked over Sunday. Only an explicit stop or a correction of what it is
            # saying cuts it off; anything else waits its turn behind it. A device that woke
            # on its own speech while it talked heard its own voice as often as his: only a
            # plain stop or a plain correction acts then, unless the voice is plainly his.
            strict = self in barge["devices"] and not (enforce and spk.get("verdict") == "accept")
            if MUTE_SAID.match(heard.strip()):
                verdict = {"decision": "stop", "reason": "mute", "by": "regex", "ms": 0}
            elif barge["early"]:
                rest, _ = bargein.strip_echo(heard, barge["saying"])
                stop = bool(bargein.STOP.match(" ".join(rest.split()))) or not rest.strip()
                verdict = {"decision": "stop" if stop else "redirect", "by": "partial", "ms": 0,
                           "reason": f"stopped at once on {barge['early']!r}" + ("" if stop else ", then answering the rest")}
            elif barge.get("verdict"):
                verdict = barge["verdict"]     # the open ear already decided: a correction
            else:
                verdict = await bargein.decide(heard, barge["saying"], barge["question"], recent=recent,
                                               strict=strict)
            decision = await self.release_barge(barge, verdict, heard, qid)
            if verdict.get("by") == "echo":
                # Nothing he said: the mic heard Sunday's own voice. Never answered.
                log.info("discarded (its own voice): %s", heard)
                speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                            "followup": followup, "discarded": "its own voice coming back through the mic",
                            "speaker": spk_done("its own voice")})
                broadcast(type="idle", id=qid)
                if not self.turns - {asyncio.current_task()} and self.vad is None:
                    await self.send(type="idle")
                return
            if verdict.get("echo"):
                log.info("took its own words out of what was heard: %s -> %s", heard, verdict["text"])
                heard = verdict["text"]
            if decision == "stop":
                await self.stop_everything("stop" if verdict["reason"] != "mute" else "mute")
            if decision == "stop" and verdict["reason"] != "mute":
                speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                            "followup": followup, "command": "stop",
                            "speaker": spk_done("stopped: a stop over its speech acts whoever says it")})
                broadcast(type="done", id=qid, command="stop")
                await self.send(type="idle")
                return
            if decision != "redirect" and enforce and action == "borderline" and verdict.get("by") != "echo" \
                    and (heard or "").strip():
                # Over its speech, with a voice that may not be his: what he said waits its turn
                # only if it was said to Sunday.
                if await directed(heard) is False:
                    await self.ignore(qid, heard, followup, "not plainly his voice, and not said to Sunday",
                                      spk_done("ignored after the directed check"))
                    return
            if decision == "redirect":
                was = barge["question"]
                redirect_note = ("(He cut you off mid-answer to correct you. You were "
                                 + (f"answering \"{was}\"" if was else "making an announcement")
                                 + (f" and had got as far as: \"{barge['saying'][-300:]}\"" if barge["saying"] else "")
                                 + ". Take what he says now as a correction of that, and answer what he actually means.)")
        if not heard:
            spk_done("nothing heard")
            broadcast(type="idle", id=qid)
            await self.send(type="idle")
            return
        said_cmd = MUTE_SAID.match(heard.strip()) or STOP_SAID.match(heard.strip()) or UNMUTE_SAID.search(heard) \
            or ELEVEN_ON_SAID.match(heard.strip()) or ELEVEN_OFF_SAID.match(heard.strip()) \
            or OVERLAY_ON_SAID.match(heard.strip()) or OVERLAY_OFF_SAID.match(heard.strip()) or dismiss
        own = None if said_cmd else bargein.own_voice(heard, " ".join(
            x for x in (recent, barge["saying"] if barge else "") if x))
        if own:
            # Its own voice, written down as if he had said it: dropped whole. No answer, and
            # nothing said about it either - not "I didn't catch that", not an apology.
            log.info("discarded (its own voice: %s): %s", own["why"], heard)
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "followup": followup, "discarded": "its own voice", "reason": own["why"],
                        "speaker": spk_done("its own voice")})
            broadcast(type="idle", id=qid)
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return
        if not barge and not always:
            # Whose voice: plainly not his is ignored without a sound; a borderline score goes
            # ahead only if the words were said to Sunday.
            if enforce and action == "reject":
                await self.ignore(qid, heard, followup, "not his voice", spk_done("ignored"))
                return
            if enforce and action == "borderline" and await directed(heard) is False:
                await self.ignore(qid, heard, followup, "not plainly his voice, and not said to Sunday",
                                  spk_done("ignored after the directed check"))
                return
        log.info("heard (%d ms to text): %s", (t_text - self.t_end) * 1000, heard)
        command = "mute" if MUTE_SAID.match(heard.strip()) else \
            "stop" if STOP_SAID.match(heard.strip()) or bargein.STOP.match(" ".join(heard.split())) else ""
        voice = "elevenlabs" if ELEVEN_ON_SAID.match(heard.strip()) else \
            "local" if ELEVEN_OFF_SAID.match(heard.strip()) else ""
        if voice:
            # Switched at once and confirmed in a few words, in the voice it switched to.
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "followup": followup, "command": "voice " + voice, "speaker": spk_done("voice switched")})
            changed = eleven.set(voice == "elevenlabs", "voice")
            if voice == "local":
                line = "Okay, back to my normal voice." if changed else "I'm already using my normal voice."
            elif not EL_KEY:
                line = "I don't have an ElevenLabs key, so I'll keep my normal voice."
            else:
                await eleven.warm()
                line = "Okay, this is my ElevenLabs voice." if changed else "I'm already using ElevenLabs."
            broadcast(type="heard", id=qid, text=heard)
            broadcast(type="reply", id=qid, text=line)
            await self.announce(line, qid, "command")
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return
        overlay = "on" if OVERLAY_ON_SAID.match(heard.strip()) else \
            "off" if OVERLAY_OFF_SAID.match(heard.strip()) else ""
        if overlay:
            # The Windows popup's overlay, switched there at once and confirmed in a few words.
            # He is heard and answered the same either way; only that screen changes.
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "followup": followup, "command": "overlay " + overlay,
                        "speaker": spk_done("overlay switched")})
            line = overlay_line(overlay == "on")
            log.info("overlay %s by voice", overlay)
            broadcast(type="overlay", on=overlay == "on", by="voice")
            broadcast(type="heard", id=qid, text=heard)
            broadcast(type="reply", id=qid, text=line)
            await self.announce(line, qid, "command")
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return
        if dismiss and not command:
            # The canvas comes down on every screen, without a model turn and without a word.
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "followup": followup, "command": "close canvas", "speaker": spk_done("closed the canvas")})
            close_canvas("voice")
            broadcast(type="heard", id=qid, text=heard)
            broadcast(type="done", id=qid, command="close")
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return
        if command:
            # Done without a word: "mute" turns the microphone off, "stop" silences him.
            speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard,
                        "followup": followup, "command": command,
                        "speaker": spk_done(f"{command}: acts whoever says it")})
            if command == "mute":
                set_muted(True, "voice")
            # Either way every speaker goes quiet now, whichever one heard him.
            await self.stop_everything(command)
            broadcast(type="done", id=qid, command=command)
            await self.send(type="idle")
            return
        # He said its name to open this listen: what follows is said to Sunday, whatever
        # the computer is playing.
        why = "" if ((wake or {}).get("named") and not followup) else \
            await not_for_sunday(heard, followup, playing or computer_playing())
        if not why and followup and not (wake or {}).get("judged") and await directed(heard) is False:
            # After a reply it listens without its name, so what it heard there is a decision: put
            # to the judge with what it just said, an answer to it or more for it is answered, and
            # him talking to someone else, or the room, is not.
            why = "not said to Sunday: the follow-up read as overheard"
        if not why and not words and wake and wake.get("by_word") and not barge \
                and spk.get("score") is not None and spk["score"] < SPEAKER_NOT_HIS \
                and await directed(heard) is False:
            # A wake nothing cleared while it listened, its name in it or not ("Sunday" and then
            # a television talking straight on): a television or the room, by both the words and
            # the voice. Either alone keeps it, and it is answered.
            why = f"not said to Sunday: the words read as overheard and the voice scored {spk['score']:.2f}"
        spk_rec = spk_done("dropped: " + why if why else "answered")
        speech_log({"kind": "heard", "at": time.time(), "qid": qid, "device": self.device, "text": heard, "followup": followup,
                    "computer_audio": playing or computer_playing(), "discarded": why or None, "speaker": spk_rec})
        if why:
            log.info("discarded (%s): %s", why, heard)
            broadcast(type="idle", id=qid)
            if not self.turns - {asyncio.current_task()} and self.vad is None:
                await self.send(type="idle")
            return
        if quiet:
            # Nothing cleared it while he spoke, and it is answered: the held chime and ring,
            # late but not lost, and only now anything on the overlay.
            if wake and self.vad is None:
                await self.send(type="speech")
            broadcast(type="status", id=qid, text="Thinking", kind="thinking")
        # He went on talking to it: the answered turn before this one was his.
        followed_up(spk)
        broadcast(type="heard", id=qid, text=heard)
        close_canvas("next question")     # a new question: the last answer's detail comes down
        # The newest question is the only one worth answering: an older one still being
        # worked on, with nothing said yet, is dropped rather than answered late.
        me = asyncio.current_task()
        me.heard_at = time.time()
        for t in list(self.turns):
            if t is not me and getattr(t, "heard_at", 0) and not getattr(t, "spoke", False) \
                    and not getattr(t, "voiced", False):
                t.cut_cause = "superseded"
                t.cancel()
                inflight.pop(getattr(t, "heard", ""), None)
        me.heard = heard
        task("start", "q-" + qid, "Question", heard, "Thinking")
        notes.log("user", heard)

        sentences: asyncio.Queue = asyncio.Queue()
        utt = utterance(qid, "followup" if followup else "reply", heard, me.heard_at)
        utt["device"] = self.device
        me.utt = utt
        speaker = asyncio.create_task(self.speak(sentences, qid, utt))
        speaker.turn = asyncio.current_task()
        spoken = []
        held = None          # what was written but not yet spoken, once a reply runs long
        message = ""         # the assistant message being written now

        async def release(text):
            """Speak the final answer, sentence by sentence, exactly once."""
            nonlocal held
            first = True
            # The first chunk goes at the earliest clause, since time to first audio is
            # the latency people hear; the rest one sentence at a time.
            lead, rest = take_speakable(text.strip(), True)
            chunks = ([lead] if lead else []) + [c for c in re.split(r"(?<=[.!?])\s+|\n+", rest) if c.strip()]
            utt["intended"] = (utt["intended"] + " " + text.strip()).strip()
            for chunk in chunks:
                chunk = voice_rules(chunk, first)
                if not chunk:
                    continue
                broadcast(type="reply", id=qid, text=(" " if spoken or held else "") + chunk)
                if held is not None:
                    held.append(chunk)
                    utt["held"].append(chunk)
                    continue
                if getattr(me, "cut_cause", ""):
                    return
                me.spoke = True
                spoken.append(chunk)
                utt["sentences"].append(chunk)
                await sentences.put(chunk)
                first = False
                if len(" ".join(spoken).split()) >= SPOKEN_WORD_CAP:
                    # Long enough to stop being a conversation: finish this sentence,
                    # keep the rest, and offer it instead of reading it all out.
                    held = []

        async def filler():
            # The main agent stays quiet until it knows. If it hasn't started its answer
            # shortly, the acknowledger's line covers the gap, once. It never says
            # anything the agent hasn't found.
            ack = asyncio.create_task(acknowledger.line(heard))
            await asyncio.sleep(FILLER_AFTER)
            # A short answer already being written is usually done in a moment; give it
            # that moment rather than covering it with a line.
            for _ in range(20):
                if spoken or not message:
                    break
                await asyncio.sleep(0.1)
            if not spoken:
                try:
                    text = await asyncio.wait_for(ack, 1.5)
                except (asyncio.TimeoutError, Exception):
                    text = ""
                # Not once he has moved on to another question, or over another answer.
                if not spoken and self.vad is None and not self.voice.locked():
                    utt["filler"].append(text or "One sec.")
                    await sentences.put(text or "One sec.")
                    broadcast(type="status", id=qid, text=text or "One sec.", kind="filler")
            else:
                ack.cancel()

        hold = asyncio.create_task(filler())
        try:
            question = shared_context(heard)
            if redirect_note:
                question = redirect_note + "\n\n" + question
            inflight[heard] = time.time()
            if self.unsaid:
                question = (f"(Only this much of your last reply was spoken before asking if he wanted the rest: "
                            f"\"{self.unsaid}\" If he wants more, carry on from there in the same short way.) {question}")
                self.unsaid = None
            thread = free_thread()
            tools: list = []        # every tool this turn ran, with what it was asked and what came back
            uses: dict = {}

            async def collect(prompt: str, watch: dict | None = None) -> str:
                """Run one message through the Sunday session and return its final answer.
                Only the last assistant message of the turn is the answer. Text in a message
                that goes on to call a tool describes the lookup and is never spoken.
                watch: the answer's canvas, followed as it is written (watch_canvas)."""
                nonlocal message
                final, answered = "", False
                async with contextlib.aclosing(thread.ask(prompt, owner=qid)) as deltas:
                    async for kind, delta in deltas:
                        if kind == "text":
                            message += delta
                            if watch is not None and not answered:
                                watch_canvas(watch, message, qid, heard)
                        elif kind == "tool":
                            broadcast(type="status", id=qid, text=TOOL_LABELS.get(delta, "Working"), kind="tool")
                            task("update", "q-" + qid, status=TOOL_LABELS.get(delta, "Working"))
                            message = ""
                        elif kind == "tool_use":
                            inp = delta.get("input") or {}
                            what = inp.get("command") or inp.get("query") or inp.get("url") or inp.get("file_path") \
                                or inp.get("prompt") or json.dumps(inp)[:300]
                            uses[delta.get("id")] = len(tools)
                            tools.append(f"{delta.get('name', '?')}: {' '.join(str(what).split())[:400]}")
                        elif kind == "tool_result":
                            out = delta.get("content")
                            if isinstance(out, list):
                                out = " ".join(c.get("text", "") for c in out if isinstance(c, dict))
                            i = uses.get(delta.get("tool_use_id"))
                            if i is not None:
                                tools[i] += (" -> ERROR " if delta.get("is_error") else " -> ") \
                                    + " ".join(str(out or "").split())[:400]
                        elif kind == "block_end" and message and not message.endswith(("\n", " ")):
                            message += "\n"
                        elif kind == "message_end":
                            if delta != "tool_use" and message.strip() and not answered:
                                answered = True
                                final = message
                            message = ""
                if message.strip() and not answered:
                    final = message
                message = ""
                return final

            # Once a canvas starts, the sentences before it are the whole spoken answer: they are
            # checked and said while the rest is still being written, and the canvas fills in on
            # his screen as it comes, so the detail never makes him wait for the answer.
            early = {"text": None, "seen": asyncio.Event(), "last": 0.0, "sig": "", "sent": False}
            collecting = asyncio.create_task(collect(question, early))
            seen = asyncio.create_task(early["seen"].wait())
            said_early = ""
            try:
                await asyncio.wait({collecting, seen}, return_when=asyncio.FIRST_COMPLETED)
                seen.cancel()
                if not collecting.done() and (early["text"] or "").strip():
                    first = canvases.trim_spoken(early["text"])
                    prog = in_progress() + lookups_out(tools)
                    verdict = await speech_gate.check(voice_rules(first, True), heard, tools, prog, "reply",
                                                      shown="a canvas, being written as this is said")
                    gate_log(verdict, first, qid, "reply", 0, heard, tools, prog)
                    if verdict["verdict"] != "broken" and first.strip():
                        said_early = first
                        hold.cancel()
                        await release(first)
                answer, visual = shown_part(await collecting, qid)
            except BaseException:
                # Cut off, superseded or failed: the session's turn goes with it, as before.
                seen.cancel()
                collecting.cancel()
                raise
            if said_early:
                answer = ""          # already said; the canvas below is all that is left
                if not visual and early["sent"]:
                    log.warning("canvas %s: the last of it was not worth a screen; leaving what is up", qid)
                elif not visual:
                    log.warning("canvas %s: said the detail was on his screen, but no canvas came: %r",
                                qid, said_early[:160])
            elif early["sent"] and not visual:
                close_canvas("not part of the answer")
            # The speech gate: nothing is said until it passes. A broken answer goes back
            # to the same session with the rule it broke, twice at most, and then only one
            # sentence of what was tried and what comes next is said.
            for attempt in range(GATE_RETRIES + 2):
                if not answer.strip():
                    if attempt:
                        answer = speech_gate.fallback_line(tools)
                    break
                prog = in_progress() + lookups_out(tools)
                verdict = await speech_gate.check(voice_rules(answer, True), heard, tools, prog, "reply",
                                                  shown=canvas_brief(visual))
                gate_log(verdict, answer, qid, "reply", attempt, heard, tools, prog)
                if verdict["verdict"] != "broken":
                    break
                if attempt == GATE_RETRIES + 1:
                    answer, visual = speech_gate.fallback_line(tools), None
                    gate_log({"verdict": "fallback", "rule": "", "why": "every answer broke the rules", "by": "code",
                              "ms": 0}, answer, qid, "reply", attempt + 1, heard, tools, prog)
                    break
                # Quiet while it works it out again: no filler, but the popup keeps showing work.
                hold.cancel()
                broadcast(type="status", id=qid, text="Rewriting the answer", kind="retry")
                task("update", "q-" + qid, status="Working")
                if attempt < GATE_RETRIES:
                    answer, again = shown_part(await collect(speech_gate.retry_prompt(verdict, answer, prog)), qid)
                else:
                    answer, again = shown_part(await collect(speech_gate.final_prompt(verdict, answer)), qid)
                    answer = speech_gate.first_sentence(answer)
                visual = again or visual
            if visual and (answer.strip() or said_early):
                # On his screen as the first words of it are said, so "it's on your screen" is true.
                show_canvas(visual, qid, "reply", heard, voice_rules(answer or said_early, True))
            if answer.strip():
                await release(answer)
            if held and " ".join(held).strip():
                utt["sentences"].append("Want the rest?")
                await sentences.put("Want the rest?")
                self.unsaid = "".join(spoken).strip()
            hold.cancel()
            task("update", "q-" + qid, status="Answering")
            await sentences.put(None)
            await speaker
            reply = "".join(spoken).strip()
            if utt.get("suppressed"):
                # Only what went out: a line the repeat guard refused was never said, and a reply
                # it refused whole is silence, with no follow-up window after it.
                reply = "".join(c for c in spoken if c in utt["sentences"]).strip()
            log_said(utt)
            broadcast(type="done", id=qid)
            task("end", "q-" + qid)
            notes.log("assistant", (reply if reply or not utt.get("suppressed")
                                    else "[said nothing: all of it repeated what he had just heard]")
                      + (f" [on his screen: {visual['title'] or 'the detail'}]" if visual else ""))
            last_turn["heard"] = heard
            inflight.pop(heard, None)
            if reply and not barge and segment is not None and spk.get("emb") is not None \
                    and (spk.get("speech_s") or 0) >= 1.0 and action != "reject" \
                    and (voiceprint.centroid is not None or not (playing or computer_playing())):
                # Answered: his, if he goes on to follow it up and it sounds like him (with a
                # computer playing, only once there is a voiceprint to check it against).
                passive["pending"] = {"answered_at": time.time(), "spk": spk,
                                      "meta": {"device": self.device, "seconds": spk.get("speech_s"), "text": heard,
                                               "score": spk.get("score")}}
            if FOLLOWUP and reply and not (AUDIO_GATE and computer_playing()) and not (self.turns - {me}) \
                    and (self.vad is None or (self.ear and self.barge is not None and not self.words_ok)):
                # A short window to keep talking without the wake word, after every reply. A Mac
                # listen over the reply that no words have cleared does not cost it: the Mac opens
                # the window once that listen closes, and drops it if his words were taken.
                await self.send(type="listen")   # the cube answers with "wake" once it has finished speaking
        except asyncio.CancelledError:
            inflight.pop(heard, None)
            hold.cancel()
            speaker.cancel()
            cause = getattr(me, "cut_cause", "") or "disconnect"
            log_said(utt, cause)
            if cause == "superseded" and not spoken:
                log.info("dropped a stale answer to: %s", heard)
                notes.log("assistant", "[superseded by a newer question, not answered]")
            else:
                notes.log("assistant", "".join(spoken).strip() + " [interrupted]")
            broadcast(type="done", id=qid, interrupted=True, cause=cause)
            task("end", "q-" + qid)
            raise
        except Exception as e:
            inflight.pop(heard, None)
            hold.cancel()
            speaker.cancel()
            log.exception("turn failed")
            log_said(utt, "error")
            broadcast(type="done", id=qid, error=str(e)[:200])
            task("end", "q-" + qid)
            await self.send(type="error", text=str(e)[:200])

    async def announce(self, text: str, qid: str, source: str = "say", since: float | None = None):
        """Speak an announcement. The caller has already put it on the popup under qid.
        since: where the repeat guard starts comparing (repeat_guard.since_for); left out,
        a command's confirmation or the voice request only refuses what is said with it."""
        me = asyncio.current_task()
        me.qid = qid
        self.announcing.add(me)
        utt = utterance(qid, source)
        utt["device"] = self.device
        utt["intended"] = text
        utt["since"] = since if since is not None else repeats.since_for(guard, source, time.time())
        me.utt = utt
        self.t_end = time.monotonic()
        q: asyncio.Queue = asyncio.Queue()
        for chunk in re.split(r"(?<=[.!?])\s+", text):
            utt["sentences"].append(chunk)
            await q.put(chunk)
        await q.put(None)
        try:
            await self.speak(q, qid, utt)
            log_said(utt)
        except asyncio.CancelledError:
            log_said(utt, getattr(me, "cut_cause", "") or "disconnect")
            raise
        finally:
            self.announcing.discard(me)
            broadcast(type="done", id=qid)

    async def say_late(self, item: "LateItem") -> str:
        """Say a late answer here, and follow it until the last of it has played: sent is not
        heard. "heard" when every sound of it played at full volume, "repeat" when he had
        already heard all of it and nothing went out, "stop" when he stopped it; otherwise
        what kept him from hearing it, and then none of it counts as said, so it can be said
        again in full."""
        me = asyncio.current_task()
        me.qid = item.qid
        self.announcing.add(me)
        utt = utterance(item.qid, item.source, item.about, item.asked or 0.0)
        utt.update(device=self.device, intended=item.text, since=item.since, late_try=item.tries,
                   came=list(item.came))
        # Up to its first sound it also waits for his newer question to be answered.
        waited = time.monotonic()
        utt["_wait"] = lambda: bool(late_busy(waited))
        me.utt = utt
        self.t_end = time.monotonic()
        q: asyncio.Queue = asyncio.Queue()
        for chunk in re.split(r"(?<=[.!?])\s+", item.text):
            utt["sentences"].append(chunk)
            await q.put(chunk)
        await q.put(None)
        why = ""
        try:
            await self.speak(q, item.qid, utt)
            began = utt.get("_first")
            if began is None:
                log_said(utt)
                return "repeat"
            while True:
                dev = self.live()
                now = time.monotonic()
                if dev.turned_down >= began:
                    why = f"{dev.turned_down_why} {dev.turned_down - began:.1f} s in"
                elif dev.vad is not None and dev.words_ok:
                    why = f"he spoke to it {now - began:.1f} s in"
                elif self.lost_at >= began or dev.lost_at >= began or not dev.is_open():
                    why = "its link dropped"
                elif now >= dev.play_until and not dev.sounding or now > dev.play_until + 10:
                    break
                if why:
                    break
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            cause = getattr(me, "cut_cause", "") or "disconnect"
            log_said(utt, cause, unheard=True)
            broadcast(type="done", id=item.qid, interrupted=True, cause=cause)
            return "stop" if cause in ("stop", "mute") else f"cut off ({cause})"
        finally:
            self.announcing.discard(me)
        if why:
            log_said(utt, "talked over: " + why, unheard=True)
            broadcast(type="done", id=item.qid, interrupted=True, cause="talked over")
            return why
        log_said(utt)
        broadcast(type="done", id=item.qid)
        return "heard"

    async def spoken(self, qid: str, until: float):
        """Tell the popup when this query's reply has finished playing on the cube, or
        was cut off, so its card stays up for as long as it is being heard."""
        sounding[qid] = sounding.get(qid, 0) + 1
        try:
            while (left := min(until, self.live().play_until) - time.monotonic()) > 0:
                await asyncio.sleep(min(left, 0.5))
        finally:
            sounding[qid] -= 1
            if sounding[qid] <= 0:
                sounding.pop(qid, None)
        broadcast(type="spoken", id=qid)

    async def speak(self, sentences: asyncio.Queue, qid: str | None = None, utt: dict | None = None):
        """Speak sentences as they arrive. The speaker is taken only while there is
        something to say and never while he is talking, so a slow answer can wait
        while another question is asked and answered."""
        loop = asyncio.get_running_loop()
        me = asyncio.current_task()
        turn = getattr(me, "turn", None)
        qid = qid or getattr(turn, "qid", None)
        holding = False
        said = False
        try:
            while (chunk := await sentences.get()) is not None:
                text = speakable(chunk)
                if not text:
                    continue
                # The repeat guard: a line that repeats what he has already heard is never said,
                # whichever path sent it. First the free check, before any voice is made for it.
                if not await vet_line(chunk, text, utt, self.device, final=False):
                    continue
                pieces = clauses(text)
                cursor = 0
                seq = None
                vetted = refused = False
                for n, piece in enumerate(pieces):
                    # Made while the cube is still playing the piece before it. ElevenLabs
                    # arrives in chunks and each goes to the cube as it lands; Kokoro in one.
                    at = max(text.find(piece, cursor), 0)
                    cursor = at + len(piece)
                    audio = tts_stream(piece, text, at)
                    pcm = await anext(audio, b"")
                    if not pcm:
                        continue
                    if not holding:
                        # He is talking, or another device in the room is listening for him:
                        # either way this answer waits rather than playing into an open mic.
                        # A late answer also waits while his newer question is being answered.
                        since = time.monotonic()
                        while self.live().vad is not None or (others_listening(self.live()) and
                                                              time.monotonic() - since < OTHER_MIC_WAIT) \
                                or (utt is not None and "_wait" in utt and utt["_wait"]()):
                            await asyncio.sleep(0.2)
                        await self.voice.acquire()
                        holding = True     # from here a cancel gives the speaker back (finally)
                        # Then the deciding check, with the speaker held and just before the
                        # first sound, against everything really said by now on every speaker.
                        vetted = True
                        refused = not await vet_line(chunk, text, utt, self.device)
                        if refused:
                            holding = False
                            self.voice.release()
                        else:
                            if turn:
                                turn.talking = True
                                turn.voiced = True
                            ms = int((time.monotonic() - self.t_end) * 1000)
                            last_turn.update(at=time.time(), latency_ms=ms)
                            log.info("audio %d ms after end of speech", ms)
                            if utt is not None:
                                utt.setdefault("_first", time.monotonic())
                            await self.speech(json.dumps({"type": "speak_start", "rate": OUT_RATE}))
                            broadcast(type="speaking", id=qid, device=self.device, ear=self.ear)
                    elif not vetted:
                        vetted = True
                        refused = not await vet_line(chunk, text, utt, self.device)
                    if refused:
                        with contextlib.suppress(Exception):
                            await audio.aclose()
                        break
                    if seq is None:
                        seq = next(_seqs)
                    # Tell the cube what it is about to say, so its own "Sunday" or "stop" is never taken for his.
                    await self.speech(json.dumps({"type": "sentence", "text": piece, "seq": seq, "boot": BOOT}), seq)
                    tagged = bool(TRIGGER_SAID.search(piece.lower()))
                    began = max(time.monotonic(), self.live().play_until)
                    while pcm:
                        for i in range(0, len(pcm), 8192):
                            await self.speech(pcm[i:i + 8192], seq)
                        # The cube says nothing when playback ends, so keep its clock here.
                        dev = self.live()
                        dev.play_until = max(time.monotonic(), dev.play_until) + len(pcm) / (OUT_RATE * 2)
                        self.box.ends[seq] = dev.play_until
                        if tagged:
                            # Another device's wake word can hear this one say "Sunday".
                            dev.trigger_until = dev.play_until + 1.0
                        said = True
                        if utt is not None:
                            dev.last_utt = utt
                        pcm = await anext(audio, b"")
                    # When each clause plays, so what the open ear hears can be checked against it.
                    self.live().timeline.append((began, self.live().play_until, piece))
                    await self.speech(json.dumps({"type": "clause_end", "seq": seq}), seq)
                if refused:
                    # Not a sound of it went out. The speaker goes back if nothing else is ready.
                    if holding and sentences.empty():
                        await self.speech(json.dumps({"type": "speak_end"}))
                        holding = False
                        if turn:
                            turn.talking = False
                        self.voice.release()
                    continue
                if seq is not None:
                    # The cube plays a sentence it holds whole, and drops one it lost half of.
                    await self.speech(json.dumps({"type": "sentence_end", "seq": seq}), seq)
                if utt is not None:
                    utt["_sent"].append((text, "filler" if chunk in utt["filler"] else "sentence",
                                         self.live().play_until))
                if sentences.empty():
                    # Nothing more ready: give the speaker back while the rest is worked out.
                    await self.speech(json.dumps({"type": "speak_end"}))
                    holding = False
                    if turn:
                        turn.talking = False
                    self.voice.release()
            if holding:
                await self.speech(json.dumps({"type": "speak_end"}))
        finally:
            if holding:
                if turn:
                    turn.talking = False
                self.voice.release()
            if said and qid:
                asyncio.get_running_loop().create_task(self.spoken(qid, self.live().play_until))
        if not self.turns - {turn} and self.live().vad is None:
            await self.send(type="idle")
            broadcast(type="idle")


# ---------------------------------------------------------------- server

cubes: set = set()
orphans: dict = {}       # device -> its dropped connection, waiting RESUME_WAIT for it to come back
viewers: set = set()
sounding: dict = {}      # qid -> how many cubes are still playing its reply
TRIGGER_SAID = re.compile(r"\b(sunday|stop)\b")
SUNDAY_FIRST = re.compile(r"^\W*(?:(?:hey|okay|ok|oh|um|uh|so|yo)[,.!]?\s+)?sunday\b", re.I)
over_live: dict = {}     # id -> what an open ear is following or deciding
over_claims: list = []   # (segment id, start, end, device) of what the open ears have acted on


OVER_SAVED = Path("/tmp/over")   # the service's private /tmp: gone when it stops


def keep_over(audio: np.ndarray, device: str, heard: str, name: str = ""):
    """The last few stretches the open ears heard, and the last whole stretch of each
    device's open ear, for tuning; never kept past a restart."""
    try:
        import soundfile
        OVER_SAVED.mkdir(exist_ok=True)
        stem = OVER_SAVED / f"{name or time.strftime('%H%M%S')}-{device}"
        soundfile.write(stem.with_suffix(".wav"), audio, RATE)
        stem.with_suffix(".txt").write_text(heard)
        for old in sorted(OVER_SAVED.glob("*.wav"))[:-30]:
            old.unlink(missing_ok=True)
            old.with_suffix(".txt").unlink(missing_ok=True)
    except Exception:
        pass


def said_to_sunday(text: str) -> str:
    """What he said from the first sentence that starts with Sunday's name, or ""."""
    parts = bargein.sentences(text)
    for i, part in enumerate(parts):
        if SUNDAY_FIRST.match(part):
            return " ".join(parts[i:])
    return ""


def words_past_name(text: str, scored: list | None = None) -> bool:
    """Real words in a transcript besides its name: "Sunday." on its own asks for nothing."""
    rest = [w for w in nonspeech.words(text) if not nonspeech.SUNDAY_LIKE.match(w)]
    sure = [(w, c) for w, c in (scored or []) if not nonspeech.SUNDAY_LIKE.match(w)]
    return bool(rest) and not nonspeech.junk(" ".join(rest), sure or None)


def said_around(devices: list, a: float, b: float) -> str:
    """What these devices were playing between a and b, by the clock their speech was sent on."""
    return " ".join(text for d in devices for start, end, text in list(d.live().timeline) if end >= a and start <= b)


def over_claim(seg: dict, dev) -> bool:
    """One stretch of his speech is acted on once, by whichever device's open ear decided it first."""
    now = time.monotonic()
    over_claims[:] = [c for c in over_claims if now - c[2] < 30]
    t1 = seg["t1"] if seg["ended_at"] else now
    for sid, a, b, d in over_claims:
        if sid == id(seg):
            return True
        if d is not dev and a < t1 + 0.5 and seg["t0"] < b + 0.5:
            return False
    over_claims.append((id(seg), seg["t0"], t1, dev))
    return True


def over_woken():
    """A wake (its word, or a follow-up window) takes over: what the open ears are following
    is his question to it now, not something said over it. Each is still read to its end,
    for a plain stop only: the words before the wake are not the wake's to hear (a stop can
    set the wake word off itself), and a stop is the same whoever acts on it. Each gives
    back the volume it took unless the wake's barge-in holds it."""
    arb.woke_at = time.monotonic()
    for c in list(cubes):
        seg = c.over_seg
        if seg is not None and not seg["dropped"] and not seg["ignore"]:
            seg["gated"] = "a wake took it over"
            if seg["ducked"]:
                asyncio.get_running_loop().call_later(
                    1.0, lambda c=c, seg=seg: asyncio.get_running_loop().create_task(c.over_release(seg)))


class Arbiter:
    cube_woke = asyncio.Event()
    cube_woke_at = -1e9
    ear_cleared_at = -1e9
    ear_dev = None              # the Mac connection that last went ahead
    woke_at = -1e9


arb = Arbiter()


def others_saying_trigger(me) -> bool:
    now = time.monotonic()
    return any(c is not me and c.trigger_until > now for c in cubes)


def cubes_hearing(at: float | None = None) -> list:
    """Home cubes whose own voice the Mac could have heard as a wake that arrived at `at`
    (now by default): connected, not muted by the button, and audible in the room as the cube
    reports it - playing, or quiet for less than EAR_FIRE_LAG, its echo tail included. While
    one is, a Mac wake stands down. Once it has gone quiet the Mac's wake is his, and the
    usual wait for the cube to report the same wake decides. A cube that has never reported
    goes by the clock here: open ear on, speech being sent, or playback not yet over plus
    the ear tail."""
    now = time.monotonic()
    at = now if at is None else at
    return [c for c in speakers() if not c.ear and not c.hw_muted and c.is_open()
            and ((c.sounding or at - c.heard_until < EAR_FIRE_LAG) if c.sounding is not None
                 else (c.over_vad is not None or c.voice.locked() or now < c.play_until + CUBE_EAR_TAIL))]


def said_lately(since: float, listener=None, own_after: float | None = None) -> str:
    """What every device has played that was still playing at `since` or began after it, up
    to now, oldest first, by the clock its speech was sent on. With own_after, what the
    listening device itself had finished playing by then is left out: a device woken while
    quiet, or opening a follow-up window once its playback is over, cannot have heard it, and
    his answer to its own offer or question repeats its words."""
    now = time.monotonic()
    mine = listener.live().timeline if listener is not None else None
    seen, out = set(), []
    for d in list(cubes) + list(orphans.values()):
        tl = d.live().timeline
        if id(tl) in seen:
            continue
        seen.add(id(tl))
        for start, end, text in list(tl):
            if end < since or start > now:
                continue
            if own_after is not None and tl is mine and end < own_after:
                continue
            out.append((start, text))
    return " ".join(t for _, t in sorted(out, key=lambda x: x[0]))


def others_listening(me) -> bool:
    """Another device in the room has its mic open. Its mic has no reference for this
    one's speaker, so anything said now is heard there as him asking it."""
    return any(c is not me and c.vad is not None and not c.away and not c.hw_muted
               for c in speakers())
computers: dict = {}   # popup connection -> (machine, sound playing)


def computer_playing() -> bool:
    return any(playing for _, playing in computers.values())


def playing_machines() -> list:
    return sorted({m for m, playing in computers.values() if playing})


overlays: dict = {}   # popup connection -> its overlay switch, from the Windows popup


def set_overlay_state(ws, d: dict):
    state = {"machine": str(d.get("machine", "computer"))[:40], "shown": bool(d.get("shown")),
             "game": str(d.get("game") or "")[:80] or None, "off": bool(d.get("off")), "game_on": bool(d.get("gameOn"))}
    if overlays.get(ws) != state:
        log.info("overlay on %s: %s%s", state["machine"], "shown" if state["shown"] else "hidden",
                 f" ({state['game']} running)" if state["game"] else "")
    overlays[ws] = state


def overlay_line(on: bool) -> str:
    """The few words that answer "overlay on" or "overlay off", from what the Windows popup
    last reported, before the switch reaches it."""
    pcs = list(overlays.values())
    if not pcs:
        return "The Windows computer isn't connected, so there's no overlay there to change."
    game = any(p["game"] for p in pcs)
    if all(p["shown"] == on for p in pcs) and not (not on and game):
        return "The overlay's already " + ("on." if on else "off.")
    if on:
        return "Okay, the overlay's on over your game until it ends." if game else "Okay, the overlay's back on."
    return "Okay, the overlay's off." + ("" if game else " It stays off until you turn it back on.")


def tell_computer_audio():
    """Every device hears whether a computer plays, and which: the Mac ear listens through
    voice processing only while its own Mac does."""
    for cube in list(cubes):
        asyncio.get_running_loop().create_task(
            cube.send(type="computer_audio", playing=computer_playing(), machines=playing_machines()))


def set_computer_audio(ws, machine: str, playing: bool):
    old = computers.get(ws)
    computers[ws] = (machine, playing)
    if not old or old[1] != playing:
        log.info("computer audio: %s %s", machine, "playing" if playing else "quiet")
        tell_computer_audio()
TOOL_LABELS = {
    "Bash": "Running a command", "WebSearch": "Searching the web", "WebFetch": "Reading a page",
    "Read": "Reading a file", "Grep": "Searching files", "Glob": "Searching files",
    "Edit": "Editing a file", "Write": "Writing a file", "Task": "Starting a helper", "Agent": "Starting a helper",
}


# Muted by voice ("mute", "mute the microphone"). While muted the cube still hears its
# wake word, but the only thing acted on after it is "unmute"; everything else is
# dropped unanswered. The state is kept on disk, so a restart stays muted: he asked
# for the microphone off, and a restart quietly turning it back on would break that.
MUTE_FILE = DATA / "muted.json"
MUTE_SAID = re.compile(r"^(?:hey )?(?:sunday[,.!]? )?(?:please )?(?:mute|go mute|be muted)"
                       r"(?: (?:the|your) (?:microphone|mic)| (?:the |your )?(?:microphone|mic)| yourself)?(?: please)?[.!]?$", re.I)
UNMUTE_SAID = re.compile(r"\bun-?mute\b|\bunmuted\b", re.I)
# "Sunday, use ElevenLabs" / "Sunday, switch to the normal voice". The recognizer writes the
# name several ways: ElevenLabs, Eleven Labs, 11 Labs, eleven lab.
_EL = r"(?:eleven|11)[ -]?labs?"
_PRE = r"^(?:hey )?(?:sunday[,.!]? )?(?:please |okay,? |ok,? )?(?:can you |could you )?"
_POST = r"(?: (?:now|instead|again))?(?: please)?[.!?]?$"
ELEVEN_ON_SAID = re.compile(
    _PRE + r"(?:use|switch(?:ed)? to|change(?:d)? to|go to|turn(?:ed)? on|switch(?:ed)? on|enable|try) (?:the |your )?" + _EL
    + r"(?: voice| mode)?" + _POST, re.I)
ELEVEN_OFF_SAID = re.compile(
    _PRE + r"(?:(?:use|switch(?:ed)?(?: back)? to|change(?:d)?(?: back)? to|go back to|back to|go to) "
    r"(?:the |your |my )?(?:normal|regular|usual|old|local|default|original|own|kokoro) voice"
    r"|(?:turn(?:ed)? off|switch(?:ed)? off|disable|stop using) (?:the )?" + _EL + r"(?: voice| mode)?)" + _POST, re.I)
# "Sunday, turn the overlay off" / "overlay on": the popup on the Windows computer. It goes
# dark there, or comes back, even over a game, until he turns it off or the game ends.
_OVL = (r"(?:the |your |my |sunday'?s )?(?:windows |computer |pc |desktop |screen )?(?:overlay|over ?lay|pop-? ?up)"
        r"(?: on (?:the |my )?(?:windows|windows computer|pc|computer|desktop|screen))?")
_SET = r"(?:turn(?:ed)?|switch(?:ed)?|put|set)"
_WHERE = r"(?: on (?:the |my )?(?:windows|windows computer|pc|computer|desktop|screen))?"
OVERLAY_ON_SAID = re.compile(
    _PRE + r"(?:" + _SET + r" (?:back )?on " + _OVL + r"|" + _SET + r" " + _OVL + r" (?:back )?on"
    r"|(?:show|enable|unhide|bring back) " + _OVL + r"|bring " + _OVL + r" back|" + _OVL + r" (?:back )?on)"
    + _WHERE + r",?" + _POST, re.I)
OVERLAY_OFF_SAID = re.compile(
    _PRE + r"(?:" + _SET + r" off " + _OVL + r"|" + _SET + r" " + _OVL + r" off|(?:hide|disable) " + _OVL
    + r"|" + _OVL + r" off)" + _WHERE + r",?" + _POST, re.I)
STOP_SAID = re.compile(r"^(?:hey )?(?:sunday[,.!]? )?(?:stop|stop (?:it|that|talking)|quiet|be quiet|shut up|"
                       r"that's enough|enough)(?: please)?[.!]?$", re.I)


def load_muted() -> bool:
    try:
        return bool(json.loads(MUTE_FILE.read_text()).get("muted"))
    except (OSError, ValueError):
        return False


muted = load_muted()


def set_muted(on: bool, by: str):
    """Mute or unmute by voice, endpoint, popup or button, and tell everyone."""
    global muted
    if on == muted:
        return
    muted = on
    try:
        MUTE_FILE.write_text(json.dumps({"muted": on, "at": time.time(), "by": by}))
    except OSError as e:
        log.warning("could not save the mute state: %s", e)
    log.info("%s by %s", "muted" if on else "unmuted", by)
    broadcast(type="mute", muted=on)
    for cube in list(cubes):
        asyncio.get_running_loop().create_task(cube.send(type="mute", muted=on))
        if on:
            cube.cut_off("mute")
            if cube.vad is not None:
                cube.vad = None
                asyncio.get_running_loop().create_task(cube.send(type="stop_listening"))
            asyncio.get_running_loop().create_task(cube.send(type="stop_audio"))


_qids = itertools.count(1)
_seqs = itertools.count(1)   # every sentence sent to a cube, so a reconnect knows where to resume


class Outbox:
    """Every frame of speech sent to one cube lately, in order, each tagged with its
    sentence, so a cube that drops off can be sent again what it did not get whole."""

    KEEP = RESUME_WAIT + 30

    def __init__(self):
        self.frames: list = []   # (time, seq or None, data)
        self.base = 0            # absolute index of frames[0]
        self.floor = 0           # nothing before this is ever sent again (stopped)
        self.ends: dict = {}     # seq -> when it should finish playing

    @property
    def end(self) -> int:
        return self.base + len(self.frames)

    def add(self, seq, data):
        now = time.monotonic()
        self.frames.append((now, seq, data))
        drop = 0
        while drop < len(self.frames) and now - self.frames[drop][0] > self.KEEP:
            drop += 1
        if drop:
            del self.frames[:drop]
            self.base += drop
            self.ends = {k: v for k, v in self.ends.items() if v > now - self.KEEP}

    def frame(self, i: int):
        return self.frames[i - self.base][2]

    def forget(self):
        self.floor = self.end

    def seq_at(self, i: int):
        return self.frames[i - self.base][1] if self.base <= i < self.end else None

    def resume_point(self, have, lost_at: float):
        """Where to start sending again: the first frame of the first sentence the cube
        does not hold whole. With no word from the cube, the first sentence that had not
        finished playing when the link dropped."""
        now = time.monotonic()
        for i in range(max(self.base, self.floor), self.end):
            t, seq, _ = self.frames[i - self.base]
            if seq is None or now - t > RESUME_WAIT:
                continue
            if (seq > have) if have is not None else (self.ends.get(seq, now) > lost_at):
                return i
        return None

    def audio_seconds(self, a: int, b: int) -> float:
        return sum(len(d) for _, _, d in self.frames[max(a, self.base) - self.base:b - self.base]
                   if isinstance(d, bytes)) / (OUT_RATE * 2)
_turns = itertools.count(1)
SPEECH_LOG = DATA / "speech.jsonl"
SPEECH_LOG_MAX = int(os.environ.get("VOICE_SPEECH_LOG_MAX", str(5 << 20)))
SPEECH_LOG_KEEP = 5


def speech_log(record: dict):
    """One JSON line per thing heard and per thing meant to be said, rotated by size."""
    try:
        if SPEECH_LOG.exists() and SPEECH_LOG.stat().st_size > SPEECH_LOG_MAX:
            for i in range(SPEECH_LOG_KEEP - 1, 0, -1):
                older = SPEECH_LOG.with_name(f"{SPEECH_LOG.name}.{i}")
                if older.exists():
                    older.replace(SPEECH_LOG.with_name(f"{SPEECH_LOG.name}.{i + 1}"))
            SPEECH_LOG.replace(SPEECH_LOG.with_name(SPEECH_LOG.name + ".1"))
        with open(SPEECH_LOG, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("speech log: %s", e)
    if record.get("kind") == "heard" and not record.get("discarded") and not record.get("command"):
        # Words of his that are answered: the repeat guard keys what may be said again on them.
        guard.heard(record.get("at") or time.time(), record.get("text") or "")
    if record.get("kind") == "heard" and not record.get("discarded") and record.get("device"):
        # Where he is: a late answer is said on the device he last spoke to.
        heard_on[record["device"]] = record.get("at") or time.time()


REPLIES = HERE / "data" / "replies"     # relay.py's record of each lookup: asked, answered, taken


def lookups_out(tools: list) -> list:
    """The lookups this turn was told are still out, as they stand now. One that has come
    back since - to a later asking of the same question, or on its own - is no longer
    in progress, so the speech gate is never told an answered lookup is still out."""
    out = []
    for key in dict.fromkeys(re.findall(r"STILL CHECKING \(lookup ([0-9a-f]{16})\)", "\n".join(tools))):
        try:
            st = json.loads((REPLIES / f"ask-{key}.json").read_text())
        except (OSError, ValueError):
            continue    # gone: it came back empty and was cleared
        if not st.get("answer"):
            out.append(f"a lookup that has not come back: {' '.join(str(st.get('question', '')).split())[:200]}")
    return out


def in_progress() -> list:
    """Work not finished yet, as the speech gate reads it for rule D: everything in the
    queue except the questions themselves."""
    return [f"{e['kind']}: {e['label']} ({e['status']})" for e in active.values() if e.get("kind") != "Question"]


def gate_log(verdict: dict, text: str, qid, source: str, attempt: int = 0, question: str = "",
             tools=(), prog=()):
    """Every gate verdict goes to the speech log; a broken one or a failure to judge to the main log too."""
    speech_log({"kind": "gate", "at": time.time(), "qid": qid, "source": source, "attempt": attempt,
                "verdict": verdict["verdict"], "rule": verdict.get("rule", ""), "why": verdict.get("why", ""),
                "by": verdict.get("by", ""), "flags": verdict.get("flags", []), "ms": verdict.get("ms", 0),
                "text": text.strip(), "question": question, "tools": list(tools)[-30:], "in_progress": list(prog)})
    if verdict["verdict"] == "broken":
        log.warning("speech gate: %s %s attempt %d broke rule %s (%s): %s", source, qid, attempt,
                    verdict["rule"], verdict.get("why", ""), text.strip()[:200])
    elif verdict["verdict"] in ("error", "fallback"):
        log.error("speech gate: %s %s attempt %d %s (%s): %s", source, qid, attempt, verdict["verdict"].upper(),
                  verdict.get("why", ""), text.strip()[:200])
    elif verdict.get("by") == "judge":
        log.info("speech gate: %s %s attempt %d passed the judge in %d ms", source, qid, attempt, verdict.get("ms", 0))


async def announcement_passes(text: str, qid, source: str, about: str = "") -> bool:
    """An announcement is said only when it keeps the rules; a broken one is dropped and logged."""
    prog = in_progress()
    verdict = await speech_gate.check(text, about, [], prog, "late answer" if source == "late" else "announcement")
    gate_log(verdict, text, qid, source, 0, about, [], prog)
    return verdict["verdict"] != "broken"


def utterance(qid, source: str, question: str = "", asked_at: float = 0.0) -> dict:
    """What one answer or announcement meant to say; speak() fills in what went out."""
    return {"kind": "said", "at": time.time(), "turn": next(_turns), "qid": qid, "source": source,
            "question": question, "asked_at": asked_at or None, "intended": "", "sentences": [],
            "filler": [], "held": [], "_sent": [], "logged": False}


def log_said(utt: dict, cause: str = "", unheard: bool = False):
    """Write the utterance once: what was meant, what played, and where and why it stopped.
    unheard: a late answer turned down or cut off for him, so none of it reached him and none
    of it counts as said, however much of it finished playing."""
    if utt.get("logged"):
        return
    utt["logged"] = True
    now = time.monotonic()
    sent = utt.pop("_sent", [])
    if unheard:
        sent = [(t, kind, float("inf")) for t, kind, _ in sent]
    words = [t for t, kind, _ in sent if kind == "sentence"]
    played = [t for t, kind, end in sent if kind == "sentence" and (not cause or end <= now)]
    rec = {k: v for k, v in utt.items() if k != "logged" and not k.startswith("_")}
    rec.update(end_at=time.time(), queued=len(utt["sentences"]), sent=len(words), played=len(played),
               filler_played=[t for t, kind, end in sent if kind == "filler" and (not cause or end <= now)])
    if cause:
        rec["interrupted"] = {"cause": cause, "at": time.time(), "after_sentence": len(played),
                              "unspoken": " ".join(utt["sentences"][len(played):])}
    speech_log(rec)
    # What never finished playing was not heard, so it no longer counts as said.
    guard.settle(utt["qid"], utt.get("device"), [t for t, _, end in sent if not cause or end <= now])


# ---------------------------------------------------------------- the repeat guard
#
# Nothing goes out of a speaker that repeats or restates what he has already heard since he
# last asked for it: repeat_guard.py holds the rule, and CubeSession.speak puts every line
# through vet_line, whichever path produced it. The guard learns what he asked from the
# speech log (speech_log above) and what was said from speak itself.

guard = repeats.Guard()
REPEAT_LOCK = asyncio.Lock()     # one line decided at a time, across every speaker


def repeat_refused(v: dict, line, utt: dict | None, since: float):
    """A line the repeat guard would not say: to the speech log and the main log, with the
    earlier line it repeats, and onto the utterance's own record."""
    m = v["matched"]
    speech_log({"kind": "repeat", "at": time.time(), "qid": line.qid, "device": line.device, "source": line.source,
                "line": line.kind, "text": line.text, "rule": v["rule"], "by": v.get("by", "code"),
                "ms": v.get("ms", 0), "matched": m.text, "matched_at": m.at, "matched_qid": m.qid,
                "matched_device": m.device, "since": since, "question": (utt or {}).get("question", "")})
    log.info("not said, a repeat (%s, by %s): %s | already said at %s: %s", v["rule"], v.get("by", "code"),
             line.text[:120], time.strftime("%-I:%M:%S %p", time.localtime(m.at)), m.text[:120])
    if utt is not None:
        utt.setdefault("suppressed", []).append({"text": line.text, "rule": v["rule"], "matched": m.text[:300]})


async def vet_line(chunk: str, text: str, utt: dict | None, device: str, final: bool = True) -> bool:
    """The repeat guard on one line about to go out of this speaker: True to say it.

    Not final, it is the free check made before the voice is synthesized, so an obvious
    repeat costs nothing. Final, it runs with the speaker held, just before the line's first
    sound, so it compares against what has really been said by then on every speaker; the
    judge is asked if the words call for it (with the rest of the utterance, so one answer
    costs one call at most), and a line it lets through is recorded as said. A refused line
    is logged with what it repeats, taken out of the utterance's sentences, and never said."""
    utt = utt if utt is not None else {"qid": None, "source": "say", "filler": [], "sentences": []}
    kind = "filler" if chunk in utt.get("filler", ()) else "greeting" if repeats.is_greeting(text) else "content"
    async with REPEAT_LOCK:
        now = time.time()
        line = repeats.Line(text, now, utt.get("qid"), device, kind, utt.get("source", ""))
        since = utt.get("since")
        if since is None:
            since = repeats.since_for(guard, utt.get("source", ""), now, utt.get("question") or "", utt.get("asked_at"))
        greet = repeats.greeting_since(guard, now)
        if final:
            sents = utt.get("sentences") or []
            rest = sents[sents.index(chunk) + 1:] if chunk in sents else []
            rest = [repeats.Line(speakable(s), now, line.qid, device, "content", line.source)
                    for s in rest if speakable(s) and not repeats.is_greeting(s)]
            v = (await repeats.vet(guard, [line] + rest, since, greet))[0]
        else:
            d = guard.decide(line, since, greet)
            v = {"say": True} if d["say"] else {"say": False, "rule": d["rule"], "matched": d["matched"], "by": "code"}
        if v["say"]:
            if final:
                line.at = time.time()
                guard.said(line)
            return True
    repeat_refused(v, line, utt, since)
    if kind != "filler" and chunk in utt.get("sentences", []):
        utt["sentences"].remove(chunk)
    return False


async def precheck(text: str, since: float, qid: str, source: str, use_judge: bool = True,
                   quiet: bool = False) -> tuple:
    """(what of an announcement or late answer may be said, the verdicts on what may not),
    sentence by sentence and before any speaker is sent it, so the caller learns what reached
    him. Every speaker checks each line again as it goes out. quiet: refusals are not logged."""
    now = time.time()
    parts = [p for p in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if p.strip()]
    lines = [repeats.Line(speakable(p), now, qid, "", "greeting" if repeats.is_greeting(p) else "content", source)
             for p in parts]
    async with REPEAT_LOCK:
        verdicts = await repeats.vet(guard, lines, since, repeats.greeting_since(guard, now), use_judge)
    kept, refused = [], []
    for part, line, v in zip(parts, lines, verdicts):
        if v["say"]:
            kept.append(part)
        else:
            refused.append(v)
            if not quiet:
                repeat_refused(v, line, None, since)
    return " ".join(kept), refused


async def announce_news(text: str, targets: list, checked: bool = False, answer: bool = False,
                        asked: float | None = None) -> tuple:
    """Speak an announcement, less whatever of it he has already heard. Returns (what is being
    said, why nothing is), so the caller learns whether it reached him. A plain announcement
    answers nothing he asked, so it is checked against everything said in the last half hour.
    answer: it answers a question he has just asked, so only what was said aloud since that
    question counts against it - his latest request, or `asked` when the caller gives it. It is
    a late answer then: it waits for him in the late answers (late_worker) rather than going
    out at once, and the same answer handed on through /late as well is said once."""
    if answer and (item := late_find(text)) is not None:
        return late_join(item, "/say")
    raw = text
    now = time.time()
    since = (asked or guard.last_request(now) or now - repeats.NEWS_WINDOW) if answer else \
        repeats.since_for(guard, "say", now)
    qid = new_qid()
    text, refused = await precheck(text, since, qid, "say")
    if not text:
        if not refused:
            return "", "empty"
        m = refused[0]["matched"]
        return "", (f"repeat: he already heard it at {time.strftime('%-I:%M %p', time.localtime(m.at))}: "
                    f"\"{m.text[:200]}\"")
    prog = in_progress()
    # checked=1: the Ratchet hook on the Mac already judged it against all four rules with
    # the full record of the work, which this judge cannot see; judging it again here only
    # ever refused true lines.
    verdict = {"verdict": "ok", "rule": "", "why": "checked on the Mac", "by": "hook", "flags": [], "ms": 0} \
        if checked else await speech_gate.check(text, "", [], prog, "announcement")
    gate_log(verdict, text, qid, "say", 0, "", [], prog)
    if verdict["verdict"] == "broken":
        log.warning("announcement not said: it broke the speech rules: %s", text[:160])
        return "", f"refused: rule {verdict['rule']}, {speech_gate.RULES[verdict['rule']]}. {verdict['why']} Reword it and send it again."
    source = "relay" if re.match(r"(Reply from the|The new chat about)", text) else "say"
    if answer:
        if (item := late_find(raw, text)) is not None:
            return late_join(item, "/say")
        late_add(LateItem(qid, text, raw, source, since, "", asked))
        return text, ""
    broadcast(type="announce", id=qid, text=text, source=source)
    if targets:
        reserve(text, qid, source)
    for cube in targets:
        asyncio.get_running_loop().create_task(cube.announce(text, qid, source, since=since))
    return text, ""


# ---------------------------------------------------------------- late answers
#
# An answer that comes back after its question was answered - a lookup's reply handed on by
# relay.py (/late), or a chat reading its answer out through /say?answer=1 - waits here until
# he can hear it: no mic open for him on any device, nothing talking, his newer question
# answered, then LATE_QUIET of quiet. It is said on the device he last spoke to, and counts as
# said only once the last of it has played at full volume. Turned down for him, cut off or
# lost on the way, none of it counts, and it is said again in full once he is done. The same
# answer arriving both ways is one late answer, said once. A stop or mute while it plays ends it.

TURNS_DOWN = {"duck": "turned down for him", "speech": "turned down while it listened to him",
              "go": "turned down while it listened to him", "stop_audio": "stopped"}
heard_on: dict = {}        # device -> when words of his were last answered there


class LateItem:
    """One late answer: what is said, the answer it came from, and how far it has got."""

    def __init__(self, qid: str, text: str, raw: str, source: str, since: float, about: str = "",
                 asked: float | None = None):
        self.qid, self.text, self.raw, self.source, self.since = qid, text, raw, source, since
        self.about, self.asked = " ".join((about or "").split()), asked
        self.state = "waiting"     # waiting, playing, heard, stopped, unheard
        self.tries = 0
        self.came = ["/late" if source == "late" else "/say"]
        self.done_at = 0.0


late_items: list = []
late_ready = asyncio.Event()


def late_same(a: str, b: str) -> bool:
    """Two texts are one answer: the same words, near enough, or one inside the other."""
    if not a or not b:
        return False
    x, y = repeats.Line(speakable(a)), repeats.Line(speakable(b))
    return bool(repeats.compare(x, y) or repeats.compare(y, x))


def late_find(*texts: str):
    """The late answer these texts are a copy of: one still to be said or being said, or one
    he stopped lately."""
    now = time.time()
    for item in late_items:
        if item.state in ("waiting", "playing") or item.state == "stopped" and now - item.done_at < LATE_KEEP:
            if any(late_same(t, x) for t in texts for x in (item.raw, item.text)):
                return item
    return None


def late_join(item: LateItem, way: str) -> tuple:
    """A second copy of a late answer, which is said once: (what is being said, why nothing is)."""
    item.came.append(way)
    if item.state == "stopped":
        log.info("late answer %s came again by %s: he stopped it, so it is not said again", item.qid, way)
        return "", "stopped: he stopped this answer while it played"
    log.info("late answer %s came again by %s while %s: it is said once", item.qid, way, item.state)
    return item.text, ""


def late_add(item: LateItem):
    now = time.time()
    late_items[:] = [x for x in late_items if x.state in ("waiting", "playing") or now - x.done_at < LATE_KEEP]
    late_items.append(item)
    log.info("late answer %s by %s, waiting for him: %s", item.qid, item.came[0], item.text[:120])
    late_ready.set()


def late_busy(since: float) -> str:
    """Why he cannot hear a late answer now, leaving aside what is playing: a mic open for him,
    a follow-up window about to open, or his newer question still being answered (waited on
    for LATE_TURN_WAIT from `since`)."""
    now = time.monotonic()
    devs = list(cubes)
    for c in devs:
        if c.vad is not None:
            return f"a mic is open for him on {c.device}"
    for c in devs:
        if c.listen_due > c.listen_started and now < max(c.play_until, c.listen_due) + LATE_LISTEN_GRACE:
            return f"{c.device} is about to listen for his reply"
    if now - since < LATE_TURN_WAIT:
        for c in devs:
            if any(not t.done() for t in c.turns):
                return f"his question on {c.device} is being answered"
    return ""


def late_hold(since: float) -> str:
    """Why a late answer cannot start now, or "" when he can hear it."""
    if why := late_busy(since):
        return why
    for c in list(cubes):
        if c.is_talking():
            return f"{c.device} is talking"
    return ""


def late_targets() -> list:
    """Where a late answer is said: the connected device he last spoke to within LATE_HERE (a
    cube only while he is at home), or else where an announcement goes - the cubes, and the
    Mac only when he is away from home."""
    devs = [c for c in speakers() if c.is_open()]
    away = any(c.ear and c.away for c in devs)
    now = time.time()
    for name, at in sorted(heard_on.items(), key=lambda x: -x[1]):
        if now - at > LATE_HERE:
            break
        here = [c for c in devs if c.device == name and (c.ear or not away)]
        if here:
            return here[:1]
    return [c for c in devs if not c.ear or c.away]


async def late_moment(item: LateItem) -> list:
    """Wait for a moment he can hear a late answer in; then the devices to say it on."""
    began = time.monotonic()
    quiet_at, told = None, None
    while True:
        why = late_hold(began)
        targets = [] if why else late_targets()
        if not why and not targets:
            why = "no speaker connected"
        if why:
            quiet_at = None
            if why != told:
                log.info("late answer %s held: %s", item.qid, why)
                told = why
        elif quiet_at is None:
            quiet_at = time.monotonic()
        elif time.monotonic() - quiet_at >= LATE_QUIET:
            return targets
        await asyncio.sleep(0.2)


async def late_play(item: LateItem, targets: list):
    """Say it once on each target and settle what became of it."""
    item.state, item.tries = "playing", item.tries + 1
    log.info("late answer %s: saying it on %s, try %d of %d", item.qid, ", ".join(c.device for c in targets),
             item.tries, LATE_TRIES)
    broadcast(type="announce", id=item.qid, text=item.text, source=item.source, about=item.about[:200])
    outs = await asyncio.gather(*[c.say_late(item) for c in targets], return_exceptions=True)
    outs = [o if isinstance(o, str) else f"failed ({o!r})" for o in outs]
    heard = [c.device for c, o in zip(targets, outs) if o == "heard"]
    item.state = "waiting"
    if heard:
        item.state = "heard"
        log.info("late answer %s: heard in full on %s, counted as said now", item.qid, ", ".join(heard))
        if item.source == "late":
            # Into the conversation history, so the next question knows this came back and was
            # said, rather than reading only "still checking" and asking the same thing again.
            notes.log("late", f"{item.about} -> {item.text}")
    elif all(o == "repeat" for o in outs):
        item.state = "heard"
        log.info("late answer %s: he had already heard all of it, so none of it was said", item.qid)
    elif "stop" in outs:
        item.state = "stopped"
        log.info("late answer %s: he stopped it, so it is not said again", item.qid)
    elif item.tries >= LATE_TRIES:
        item.state = "unheard"
        log.error("late answer %s: not heard in full after %d tries (%s); left unsaid", item.qid, item.tries,
                  "; ".join(outs))
    else:
        log.info("late answer %s: not heard (%s); none of it counts as said, and it is said again in full "
                 "once he is done", item.qid, "; ".join(f"{c.device}: {o}" for c, o in zip(targets, outs)))
    if item.state != "waiting":
        item.done_at = time.time()


async def late_worker():
    """Say the late answers in the order they came, one at a time, each once he can hear it."""
    while True:
        item = next((x for x in late_items if x.state == "waiting"), None)
        if item is None:
            late_ready.clear()
            await late_ready.wait()
            continue
        try:
            await late_play(item, await late_moment(item))
        except Exception:
            log.exception("late answer %s failed", item.qid)
            item.tries += item.state == "waiting"
            item.state = "unheard" if item.tries >= LATE_TRIES else "waiting"
            item.done_at = time.time()
            await asyncio.sleep(1)


def new_qid() -> str:
    """A stable id for one question or announcement, carried on every popup event about it."""
    return f"{int(started_at)}-{next(_qids)}"


def light_on() -> bool:
    """Whether any cube's ring is lit."""
    return any(c.light for c in cubes if not c.ear)


def broadcast(**event):
    """Send one event to every popup that is watching."""
    event["at"] = time.time()
    data = json.dumps(event)
    for v in list(viewers):
        asyncio.get_running_loop().create_task(_send_viewer(v, data))


async def _send_viewer(v, data):
    try:
        await v.send(data)
    except websockets.ConnectionClosed:
        viewers.discard(v)


# ---------------------------------------------------------------- the canvas

showing: dict | None = None    # the canvas up on his screens: {"id", "title", "at", "source"}
canvas_screens: dict = {}      # canvas id -> {machine: "shown" | "held: why"}, as the popups report it


def shown_part(answer: str, qid: str) -> tuple:
    """(what is said, the canvas to show or None). The canvas is cut out of the model's answer
    before the speech gate, the repeat guard or a voice sees a word of it. One too slight to need
    a screen is not shown; one that was the whole answer leaves a short line to say."""
    spoken, visual, problems = canvases.split(answer)
    if problems:
        log.warning("canvas %s: %s", qid, "; ".join(problems)[:300])
    if visual:
        worth, why = canvases.worth_showing(visual)
        if not worth:
            log.info("canvas %s not shown (%s): %s", qid, why, visual["title"][:80])
            spoken = spoken or canvases.plain_text(visual)
            visual = None
    if visual:
        # The detail is on his screen, so what is said stays a sentence or two.
        spoken = canvases.trim_spoken(spoken) or "It's on your screen."
    return spoken, visual


def canvas_brief(visual: dict | None) -> str:
    """What the speech gate is told went to his screen with an answer."""
    if not visual:
        return ""
    kinds = sorted({b["type"] for b in visual["blocks"]} - {"text", "heading", "divider"}) or ["text"]
    return f"\"{visual['title'] or 'untitled'}\", with {', '.join(kinds)}"


def show_canvas(payload: dict, qid: str, source: str, question: str = "", said: str = "",
                partial: bool = False) -> None:
    """Put a canvas on his screens. Each popup decides which screen he is at, and the Windows
    one holds it back over a game unless the overlay is on, like everything else it draws.
    partial: still being written; the same id comes again as it grows, and last whole."""
    global showing
    first = not showing or showing["id"] != qid
    showing = {"id": qid, "title": payload["title"], "at": time.time(), "source": source}
    if first:
        canvas_screens[qid] = {}
        for old in list(canvas_screens)[:-20]:
            canvas_screens.pop(old, None)
    broadcast(type="canvas", id=qid, source=source, question=" ".join(question.split())[:300],
              said=canvases.said_line(said)[:600], partial=partial, **payload)
    if first or not partial:
        log.info("canvas %s %s (%s): %s, %s", qid, "filling in" if partial else "up", source, payload["title"][:80],
                 ", ".join(b["type"] for b in payload["blocks"])[:200])
    if not partial:
        speech_log({"kind": "canvas", "at": time.time(), "qid": qid, "source": source, "title": payload["title"],
                    "blocks": [b["type"] for b in payload["blocks"]]})


def watch_canvas(watch: dict, message: str, qid: str, heard: str):
    """Follow an answer's canvas as the model writes it: what comes before it is the spoken part,
    and the canvas goes to his screen as it grows, a few times a second, once it holds enough to
    be worth a screen."""
    low = message.lower()
    at = min((i for i in (low.find("<canvas"), low.find("```canvas")) if i >= 0), default=-1)
    if at < 0:
        return
    if watch["text"] is None:
        watch["text"] = message[:at]
        watch["seen"].set()
    now = time.monotonic()
    if now - watch["last"] < 0.4:
        return
    watch["last"] = now
    _, visual, _ = canvases.split(message)
    if not visual or not canvases.worth_showing(visual)[0]:
        return
    sig = json.dumps(visual, sort_keys=True)
    if sig != watch["sig"]:
        watch["sig"], watch["sent"] = sig, True
        show_canvas(visual, qid, "reply", heard, voice_rules(canvases.trim_spoken(watch["text"]), True), partial=True)


def close_canvas(by: str) -> bool:
    """Take the canvas down on every screen: he said so, closed it on one screen, or asked again."""
    global showing
    if not showing:
        return False
    log.info("canvas %s down (%s)", showing["id"], by)
    broadcast(type="canvas_close", id=showing["id"], by=by)
    showing = None
    return True


def canvas_reopened(qid: str):
    """He brought a closed canvas back from its card: "close that" closes it again."""
    global showing
    if not showing:
        showing = {"id": qid, "title": "", "at": time.time(), "source": "reopened"}
        log.info("canvas %s up again from its card", qid)


def devices_now() -> dict:
    return {"cubes": sorted({c.device for c in cubes if not c.ear}), "ears": sorted({c.device for c in cubes if c.ear}),
            "hw_muted": sorted({c.device for c in cubes if c.hw_muted})}


active: dict = {}          # id -> the "task" event that started it, while it is in progress
TASK_MAX_AGE = 5 * 3600    # a reporter that died without saying "end" is dropped after this


def owner_of(pid: int) -> str | None:
    """The question being answered by the Claude process that pid runs under, if any."""
    procs = {c.proc.pid: c for c in pool if c.proc and c.proc.returncode is None}
    for _ in range(40):
        if pid in procs:
            return procs[pid].owner
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if pid <= 1:
            return None
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rindex(")") + 2] != "Z"    # exited, waiting on its parent
    except (OSError, ValueError, IndexError):
        return True


def task(state: str, tid: str, kind: str = "", label: str = "", status: str = "", pid: int = 0):
    """Start, update or end one item of work in the popup's queue."""
    now = time.time()
    for k in [k for k, e in active.items() if now - e["started"] > TASK_MAX_AGE]:
        active.pop(k)
        broadcast(type="task", state="end", id=k)
    if state == "start":
        owner = tid[2:] if tid.startswith("q-") else owner_of(pid) if pid else None
        if owner and not tid.startswith("q-") and "q-" + owner not in active:
            return      # its question has already ended; its work goes with it
        active[tid] = {"type": "task", "state": "start", "id": tid, "kind": kind,
                       "label": " ".join(label.split())[:120], "status": status or "Working", "started": now,
                       "pid": pid, "owner": owner}
        broadcast(**active[tid])
    elif state == "update" and tid in active:
        active[tid]["status"] = status
        broadcast(type="task", state="update", id=tid, status=status)
    elif state == "end" and active.pop(tid, None) is not None:
        broadcast(type="task", state="end", id=tid)


def end_owned(qid: str):
    """A question has ended, however it ended: so has everything it started."""
    for k, e in list(active.items()):
        if k == "q-" + qid or e.get("owner") == qid:
            task("end", k)


def live_ids() -> list:
    """Every card still in progress: an open mic, a question being worked on, an announcement."""
    ids = set()
    for c in cubes:
        if c.vad is not None and getattr(c, "qid", None):
            ids.add(c.qid)
        for t in list(c.turns) + list(c.announcing):
            if getattr(t, "qid", None):
                ids.add(t.qid)
    return sorted(ids)


def snapshot() -> dict:
    return {"active": list(active.values()), "live": live_ids(), "playing": sorted(sounding), "devices": devices_now()}


SYNC_EVERY = float(os.environ.get("VOICE_SYNC_EVERY", "10"))


async def keep_in_sync():
    """Drop items whose reporter died without saying so, and send every popup the
    truth, so nothing it shows can outlive the work behind it."""
    while True:
        await asyncio.sleep(SYNC_EVERY)
        for k, e in list(active.items()):
            if e.get("pid") and not alive(e["pid"]):
                log.info("dropping %s: its process %d is gone", k, e["pid"])
                task("end", k)
        if viewers:
            broadcast(type="sync", **snapshot())


def seed_repeats():
    """After a restart, the repeat guard reads back what was heard and said in the speech log,
    so a restart is never the reason a line he heard a minute ago is said again."""
    rows = []
    for p in (SPEECH_LOG.with_name(SPEECH_LOG.name + ".1"), SPEECH_LOG):
        try:
            for raw in p.read_text(errors="ignore").splitlines():
                with contextlib.suppress(ValueError):
                    rows.append(json.loads(raw))
        except OSError:
            continue
    guard.seed([r for r in rows if isinstance(r, dict) and time.time() - r.get("at", 0) < repeats.KEEP_FOR])
    log.info("repeat guard: %d lines said and %d requests read back from the speech log",
             len(guard.lines), len(guard.requests))


def reserve(text: str, qid: str, source: str):
    """An announcement just sent to the speakers counts as said from now, so a second copy
    arriving a second later is refused at once rather than queued behind it. Whatever of it
    never plays is taken back when the speaker finishes (Guard.settle). A late answer is never
    reserved: its second copy joins it in the late answers, and it counts as said only once
    it has played."""
    now = time.time()
    for part in [p for p in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if p.strip()]:
        guard.said(repeats.Line(speakable(part), now, qid, "",
                                "greeting" if repeats.is_greeting(part) else "content", source))


def speakers() -> list:
    """The newest connection from each cube, and a cube whose link has just dropped:
    what it is sent waits for it to come back."""
    newest = {}
    for c in cubes:
        if c.device not in newest or c.connected_at > newest[c.device].connected_at:
            newest[c.device] = c
    for device, c in orphans.items():
        newest.setdefault(device, c)
    return list(newest.values())


def is_home(addr: str) -> bool:
    """This machine, the home network, or the tailnet."""
    return addr in ("127.0.0.1", "::1") or addr.startswith(("192.168.", "10.", "100.", "::ffff:192.168."))


async def health(connection, request):
    path, _, query = request.path.partition("?")
    if path == "/say" and connection.remote_address[0] in ("127.0.0.1", "::1"):
        # Announcements from other services on this machine: GET /say?text=...
        from urllib.parse import parse_qs
        text = voice_rules(parse_qs(query).get("text", [""])[0].strip(), True)
        # Announcements are for the cube; the Mac says them only when he is away from it. An
        # answer (answer=1) is a late answer, said on the device he is using (late_targets).
        targets = [c for c in speakers() if not c.ear or c.away]
        # answer=1: it answers a question he has just asked. Him asking again is never a
        # reason to say less, so only what was said aloud since that question counts against
        # it: since his latest request, or since asked=<epoch seconds> when the caller knows.
        answer = parse_qs(query).get("answer", [""])[0] == "1"
        try:
            asked = float(parse_qs(query).get("asked", ["0"])[0]) or None
        except ValueError:
            asked = None
        spoken, reason = "", "empty" if not text else ""
        if text:
            # One announcement is one card and is spoken once on each speaker, however
            # many connections a cube has open while it reconnects, and only the part of
            # it he has not already heard. The caller waits for the verdict, so a refusal,
            # a repeat or a drop comes back to it rather than being lost here.
            try:
                spoken, reason = await asyncio.wait_for(announce_news(
                    text, targets, parse_qs(query).get("checked", [""])[0] == "1", answer, asked), 60)
            except asyncio.TimeoutError:
                reason = "timed out deciding whether to say it"
        return Response(200, "OK", Headers({"Content-Type": "application/json"}),
                        json.dumps({"ok": bool(spoken), "spoken": spoken, "reason": reason, "cubes": len(targets),
                                    "repeat": reason.startswith("repeat"),
                                    "trimmed": bool(spoken) and spoken != text}).encode())
    if path == "/show" and connection.remote_address[0] in ("127.0.0.1", "::1"):
        # A canvas on his screen from any of his sessions, the way /say speaks: ?spec=<canvas JSON>
        # (canvas.py), or ?md=<markdown>&title=.., or ?close=1. It answers with where it landed,
        # so a caller only says "it's on your screen" when a screen actually shows it.
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}

        def reply(status: int, body: dict):
            return Response(status, "OK" if status == 200 else "Bad Request",
                            Headers({"Content-Type": "application/json"}), json.dumps(body).encode())

        if q.get("close"):
            return reply(200, {"ok": True, "closed": close_canvas(q.get("by") or "endpoint")})
        if not q.get("spec") and not q.get("md"):
            return reply(200, {"ok": True, "showing": showing})
        try:
            payload = json.loads(q["spec"]) if q.get("spec") else \
                canvases.from_markdown(q["md"], q.get("title") or "", q.get("subtitle") or "")
        except ValueError as e:
            return reply(400, {"ok": False, "reason": f"spec is not JSON: {e}"})
        if isinstance(payload, dict) and q.get("title") and q.get("spec"):
            payload["title"] = q["title"]
        clean, problems = canvases.sanitize(payload)
        if not clean:
            return reply(400, {"ok": False, "reason": "; ".join(problems)})
        qid = new_qid()
        show_canvas(clean, qid, "show")
        want = len(viewers)
        for _ in range(25):
            if len(canvas_screens.get(qid, {})) >= want:
                break
            await asyncio.sleep(0.1)
        screens = dict(canvas_screens.get(qid, {}))
        return reply(200, {"ok": True, "id": qid, "shown": any(v == "shown" for v in screens.values()),
                           "screens": screens, "problems": problems})
    if path == "/late" and connection.remote_address[0] in ("127.0.0.1", "::1"):
        # A lookup that answered after its question was answered: relay.py, once it lands.
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query).items()}
        ok = bool(q.get("text", "").strip())
        if ok:
            try:
                asked = float(q.get("asked", "0")) or time.time()
            except ValueError:
                asked = time.time()
            asyncio.get_running_loop().create_task(tell_late(q.get("about", ""), q["text"].strip(), asked))
        return Response(200 if ok else 400, "OK" if ok else "Bad Request",
                        Headers({"Content-Type": "application/json"}), json.dumps({"ok": ok}).encode())
    if path == "/enroll" and connection.remote_address[0] in ("127.0.0.1", "::1"):
        # Ask him, aloud and once, for a few sentences of his voice. ?force=1 asks again.
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query).items()}
        asked = DATA / "voiceprint" / "asked.json"
        reason, dev = "", None
        home = [c for c in speakers() if not c.ear and not c.hw_muted and c.is_open()]
        if asked.exists() and q.get("force") != "1":
            reason = "already asked: " + asked.read_text()[:200]
        elif muted:
            reason = "muted"
        elif computer_playing():
            reason = "a computer is playing sound, which his answer would be mixed with"
        elif not home:
            reason = "no cube listening"
        elif any(d.is_talking() or d.vad is not None for d in speakers()):
            reason = "busy: talking or listening"
        else:
            dev = home[0]
            asked.write_text(json.dumps({"at": time.time(), "device": dev.device}))
            log.info("voiceprint: asking for his voice on %s", dev.device)
            asyncio.get_running_loop().create_task(dev.ask_for_voice())
        return Response(200, "OK", Headers({"Content-Type": "application/json"}),
                        json.dumps({"ok": dev is not None, "reason": reason,
                                    "device": dev.device if dev else None}).encode())
    if path == "/stop" and is_home(connection.remote_address[0]):
        # The same as saying "stop": every speaker goes quiet and nothing pending is said.
        devs = list(cubes)
        if devs:
            await devs[0].stop_everything("stop by endpoint")
        return Response(200, "OK", Headers({"Content-Type": "application/json"}),
                        json.dumps({"ok": bool(devs), "devices": [d.device for d in devs]}).encode())
    if path in ("/mute", "/unmute") and is_home(connection.remote_address[0]):
        set_muted(path == "/mute", "endpoint")
        return Response(200, "OK", Headers({"Content-Type": "application/json"}),
                        json.dumps({"ok": True, "muted": muted}).encode())
    if path in ("/voice", "/voice/elevenlabs", "/voice/local") and is_home(connection.remote_address[0]):
        # The voice mode: /voice reads it, /voice/elevenlabs and /voice/local switch it.
        if path != "/voice" and eleven.set(path == "/voice/elevenlabs", "endpoint") and eleven.on:
            asyncio.get_running_loop().create_task(eleven.warm())
        return Response(200, "OK", Headers({"Content-Type": "application/json"}),
                        json.dumps({"ok": True, **eleven.status()}).encode())
    if path == "/activity" and connection.remote_address[0] in ("127.0.0.1", "::1"):
        # Work reported by relay.py and transcripts.py: GET /activity?state=start&id=...
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query).items()}
        ok = bool(q.get("id")) and q.get("state") in ("start", "update", "end")
        if ok:
            pid = int(q["pid"]) if q.get("pid", "").isdigit() else 0
            task(q["state"], q["id"], q.get("kind", ""), q.get("label", ""), q.get("status", ""), pid)
        return Response(200 if ok else 400, "OK" if ok else "Bad Request",
                        Headers({"Content-Type": "application/json"}), json.dumps({"ok": ok}).encode())
    if path == "/health":
        body = json.dumps({
            "ok": True,
            "cubes": sorted(c.device for c in cubes if not c.ear),
            "ears": {c.device: {"away": c.away} for c in cubes if c.ear},
            "hw_muted": sorted(c.device for c in cubes if c.hw_muted),
            "uptime_s": int(time.time() - started_at),
            "model": MODEL,
            "voice": VOICE,
            "tts": eleven.status(),
            "last_turn": last_turn,
            "muted": muted,
            "computer_audio": {m: p for m, p in computers.values()},
            "overlay": {o["machine"]: {k: v for k, v in o.items() if k != "machine"} for o in overlays.values()},
            "active": [{"kind": e["kind"], "label": e["label"], "status": e["status"]} for e in active.values()],
            "late": [{"id": x.qid, "state": x.state, "tries": x.tries, "came": x.came, "text": x.text[:120]}
                     for x in late_items if x.state in ("waiting", "playing")],
            "speaker": {**voiceprint.status(), "on": SPEAKER,
                        "last": {k: v for k, v in speaker_last.items() if k not in ("echo",)}},
        }, default=str).encode()
        return Response(200, "OK", Headers({"Content-Type": "application/json"}), body)
    return None


async def handler(ws):
    if ws.request.path.startswith("/events"):
        # A popup: it only listens.
        viewers.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "cubes": len(cubes), **snapshot(),
                                      "muted": muted, "elevenlabs": eleven.on, "light": light_on()}))
            # It speaks to say whether its computer is playing sound and, on Windows, how its overlay stands.
            async for msg in ws:
                try:
                    d = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if d.get("type") in ("mute", "unmute"):
                    set_muted(d["type"] == "mute", "popup")
                elif d.get("type") == "voice":
                    if eleven.set(bool(d.get("elevenlabs")), "popup") and eleven.on:
                        asyncio.get_running_loop().create_task(eleven.warm())
                elif d.get("type") == "audio":
                    set_computer_audio(ws, str(d.get("machine", "computer"))[:40], bool(d.get("playing")))
                elif d.get("type") == "overlay":
                    set_overlay_state(ws, d)
                elif d.get("type") == "canvas_closed":
                    # Closed on one screen (Esc, a click, its button): down on the others too.
                    if showing and showing["id"] == d.get("id"):
                        close_canvas(str(d.get("by") or "screen")[:20])
                elif d.get("type") == "canvas_state" and d.get("id") in canvas_screens:
                    if d.get("reopened") and d.get("shown"):
                        canvas_reopened(d["id"])
                    machine = str(d.get("machine") or "computer")[:40]
                    canvas_screens[d["id"]][machine] = "shown" if d.get("shown") else \
                        "held back: " + str(d.get("why") or "")[:80]
                    log.info("canvas %s on %s: %s", d["id"], machine, canvas_screens[d["id"]][machine])
        except websockets.ConnectionClosed:
            pass
        finally:
            viewers.discard(ws)
            overlays.pop(ws, None)
            if computers.pop(ws, None) is not None:
                tell_computer_audio()
        return
    session = CubeSession(ws)
    cubes.add(session)
    await session.send(type="computer_audio", playing=computer_playing(), machines=playing_machines())
    await session.send(type="mute", muted=muted)
    try:
        await session.run()
    finally:
        cubes.discard(session)
        if session.light:
            broadcast(type="light", on=light_on())
        log.info("%s disconnected: %s", "ear" if session.ear else "cube", session.device)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for c in pool:
        await c.start()
        asyncio.get_running_loop().create_task(c.keep_warm())
    await acknowledger.start()
    asyncio.get_running_loop().create_task(acknowledger.keep_warm())
    # The directed judge now reads the words of most wakes before anything chimes: kept warm.
    await gatekeeper.start()
    asyncio.get_running_loop().create_task(gatekeeper.keep_warm())
    asyncio.get_running_loop().create_task(speech_gate.judge.keep())
    asyncio.get_running_loop().create_task(bargein.judge.keep())
    asyncio.get_running_loop().create_task(repeats.judge.keep())
    seed_repeats()
    synthesize("Ready.")
    if SPEAKER:
        try:
            voiceprint.check(np.zeros(RATE, dtype=np.float32) + 0.01 * np.random.default_rng(0).standard_normal(RATE))
            log.info("speaker check: %s", json.dumps(voiceprint.status()))
        except Exception:
            log.exception("speaker check could not start; every wake passes")
    asyncio.get_running_loop().create_task(eleven.refresh_quota())
    asyncio.get_running_loop().create_task(eleven.warm())
    asyncio.get_running_loop().create_task(keep_in_sync())
    asyncio.get_running_loop().create_task(late_worker())
    async with serve(handler, HOST, PORT, process_request=health, max_size=None, ping_interval=PING_INTERVAL,
                     ping_timeout=PING_TIMEOUT, write_limit=WRITE_LIMIT,
                     # /say waits for the news check and the speech gate before it answers.
                     open_timeout=90):
        log.info("listening on %s:%d, voice %s at %d Hz; elevenlabs %s (%s, %s, key %s)", HOST, PORT, VOICE, TTS_RATE,
                 "on" if eleven.on else "off", EL_VOICE_NAME, EL_MODEL, "present" if EL_KEY else "missing")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
