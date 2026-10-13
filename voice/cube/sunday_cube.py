#!/usr/bin/env python3
"""The cube side of Sunday.

Reads both mics and the speaker reference from one ALSA loopback stream
(hw:0,4: mic 1, mic 2, reference L, reference R), cancels the speaker's own
sound with SpeexDSP, runs the "sunday" wake word locally, streams the cleaned
mic audio to the brain on the Pi while listening, and plays what comes back
through hw:0,1. The wake word stays live while it talks: saying "sunday" over it
ducks it while he talks, and the brain decides from his words whether it stops.
"stop" and the top button silence it at once.

While it talks it is already awake: the cleaned mic goes to the brain the whole time,
with no wake word, no chime and no change to the ring, bracketed by {"type":"ear"}
on and off. The brain hears him over it and stops it only when he plainly wants it to.
"""

import asyncio
import base64
import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

HOME = Path(os.environ.get("SUNDAY_HOME", "/data/sunday"))
CONFIG = HOME / "config.json"
WAKEWORDS = Path("/usr/lib/python3.11/site-packages/wakewords")
TFLITE = Path("/usr/lib/python3.11/site-packages/pymicro_wakeword/lib/libtensorflowlite_c.so")
LED_DIR = "/usr/share/thirdreality/animation/"
KEY_FIFO = "/tmp/sunday-keys"
# For testing a change end to end: "wake <file>" acts as a heard "sunday" and sends that
# 16 kHz mono s16le file as what he said, in real time, in place of the microphone.
# "hear <file> [gain_db]" mixes the file into the microphone with no wake at all, as if
# he had said it in the room: what the wake word, the stop word and the brain all hear.
# "probe <file> [gain_db]" is "hear" for the wake word alone: it logs how close it came and
# never wakes it. "record <seconds>" keeps the raw mics and loopback in /tmp/sunday-rec.raw
# (for wake_replay.py), and "watch <seconds>" logs the gain and both feeds every 100 ms.
INJECT_FIFO = "/tmp/sunday-inject"
MIC_GPIO = "/sys/class/gpio/gpio438/value"

TRIGGER_WORDS = re.compile(r"\b(sunday|stop)\b")
RATE = 16000
FRAME = 160                 # 10 ms at 16 kHz, the unit everything below works in
OUT_RATE = 48000
OUT_FRAME = 480             # 10 ms at 48 kHz

DEFAULTS = {
    "server": "ws://192.168.1.50:8794",
    "volume": 70,
    "wake_word": "sunday",
    "echo_tail_ms": 160,
    "mic_gain_db": 18,          # the PDM gain control does nothing; this is the real gain
    "wake_cutoff": 0.75,
    "speaking_wake_cutoff": 0.85,  # its own voice leaks through the canceller; ask for less
    # While one of his computers is playing sound (the server says so), speech from it
    # can sound enough like "sunday" to wake it; ask for more.
    "playing_wake_cutoff": 0.85,
    "sure_wake": 0.95,             # at or above this a wake chimes at once
    "duck_volume": 0.35,           # how loud it keeps talking while he talks over it
}
# The jitter buffer. Speech from the brain starts once this much is queued or its whole
# sentence is in. Run dry mid-sentence (the network fell behind) and it goes quiet and waits
# for more before going on, longer each time in one reply, rather than playing the scraps
# that arrive as they come, which is what sounds robotic.
PREBUFFER_S = 0.3
REBUFFER_S = 0.8
REBUFFER_MAX_S = 4.0
LINK_SILENT_S = 15          # nothing at all from the brain this long (it pings every 5 s): reconnect
DUCK_LIMIT_S = 45           # ducked with no word from the brain this long after he stopped: full volume again
HOLD_WAKE_S = 10            # a wake heard while disconnected waits this long for the brain
EAR_HANG_S = 1.0            # the open ear stays open this long after it stops talking, for a sentence he began over it
# The wake word itself goes to the brain with the wake, for its speaker check: the 1.5 s of
# what the mic heard before the wake fired. So does how much of that its own speaker explains:
# while it plays, the echo canceller's residual in the second before the wake against the
# residual of the seconds before that. A wake with no more residual than its own voice leaves
# is its own voice.
PREROLL_FRAMES = 150
ECHO_FRAMES = 800           # 8 s of the canceller's residual while playing
WAKE_FRAMES = 100           # the wake word's own second
# Its speaker is heard in the room from the first 10 ms its loopback carries sound until the
# room has gone back to its own level after the last: the echo tail, timed at the end of every
# stretch of playback as how long the mic takes to fall to within ROOM_DB of the room's quiet
# level. The tail used is the 90th percentile of the last 20 measured, ROOM_TAIL_S until there
# are 3 of them: the 90th percentile of 17 timed after it played on 2026-10-07 (20 to 191 ms,
# median 51 ms).
ROOM_TAIL_S = 0.16
ROOM_DB = 3.0
REARM = 0.5                 # under this the wake word is not near "sunday" (the near-miss log's own bar)
FRESH_S = 3.0               # a fresh wake model hears this long after its speaker goes quiet (mic_thread)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG)


# ---------------------------------------------------------------- LED ring

# The flag is tr_ledring's "return to idle when done": off holds the animation
# for as long as the state lasts, on plays it once and goes dark.
LED = {
    "listening": ("active-waking.animation", False),
    "thinking": ("active-thinking.animation", False),
    "speaking": ("active-talking.animation", False),
    "idle": ("active-ending.animation", True),
    "muted": ("mics-off_on.animation", True),
    "off": ("none.animation", True),
}
_led_state = None
_led_listener = None       # told whether the ring is lit, so the brain can pass it on
LIT = ("listening", "thinking", "speaking")


def led(state: str):
    global _led_state
    if state == _led_state:
        return
    _led_state = state
    if _led_listener:
        _led_listener(state in LIT)
    name, to_idle = LED[state]
    subprocess.Popen(
        ["dbus-send", "--system", "--type=signal", "/com/3r/EventBus", "com._3reality.EventBus.LedShow",
         f"boolean:{'true' if to_idle else 'false'}", f"array:string:{LED_DIR}{name}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------- SpeexDSP

class EchoCanceller:
    SET_ECHO_STATE = 24
    SET_DENOISE = 0
    SET_AGC = 2
    SET_AGC_LEVEL = 6
    SET_NOISE_SUPPRESS = 18
    SET_ECHO_SUPPRESS = 20
    SET_ECHO_SUPPRESS_ACTIVE = 22
    ECHO_SET_SAMPLING_RATE = 24

    def __init__(self, tail_ms: int):
        lib = ctypes.CDLL(str(HOME / "lib" / "libspeexdsp.so.1"))
        lib.speex_echo_state_init.restype = ctypes.c_void_p
        lib.speex_preprocess_state_init.restype = ctypes.c_void_p
        lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib = lib
        self.echo = lib.speex_echo_state_init(FRAME, RATE * tail_ms // 1000)
        rate = ctypes.c_int(RATE)
        lib.speex_echo_ctl(self.echo, self.ECHO_SET_SAMPLING_RATE, ctypes.byref(rate))
        self.pre = lib.speex_preprocess_state_init(FRAME, RATE)
        self._ctl(self.SET_ECHO_STATE, ctypes.c_void_p(self.echo), raw=True)
        self._ctl(self.SET_DENOISE, ctypes.c_int(1))
        self._ctl(self.SET_NOISE_SUPPRESS, ctypes.c_int(-20))
        self._ctl(self.SET_ECHO_SUPPRESS, ctypes.c_int(-45))
        self._ctl(self.SET_ECHO_SUPPRESS_ACTIVE, ctypes.c_int(-20))
        self._ctl(self.SET_AGC, ctypes.c_int(1))
        self._ctl(self.SET_AGC_LEVEL, ctypes.c_float(12000.0))
        self.out = ctypes.create_string_buffer(FRAME * 2)

    def _ctl(self, req, val, raw=False):
        self.lib.speex_preprocess_ctl(self.pre, req, val if raw else ctypes.byref(val))

    def agc_gain_db(self) -> int:
        """The preprocessor's automatic gain now, in dB (SPEEX_PREPROCESS_GET_AGC_GAIN)."""
        v = ctypes.c_int32(0)
        self._ctl(35, v)
        return v.value

    def process(self, mic: bytes, ref: bytes) -> tuple[bytes, bytes]:
        """Return (echo-cancelled, echo-cancelled plus denoise and gain). The
        first is what speech recognition gets; the gated second suits the wake word."""
        self.lib.speex_echo_cancellation(self.echo, mic, ref, self.out)
        cancelled = self.out.raw
        self.lib.speex_preprocess_run(self.pre, self.out)
        return cancelled, self.out.raw


class Sound:
    """Whether its own speaker can be heard in the room: from the loopback of what the speaker
    actually plays, not from what was sent or queued, until the measured echo tail after it."""

    def __init__(self):
        self.on = False
        self.last_ref = -1e9          # the last 10 ms the loopback carried sound
        self.was_ref = False
        self.quiet = deque(maxlen=300)   # mic dB of the last 3 s with nothing of its own audible
        self.tails = deque(maxlen=20)
        self.timing = None            # [the speaker's last sound, the room's quiet level, quiet frames]
        self.measured = None          # the tail just timed, for the log

    def tail(self) -> float:
        if len(self.tails) < 3:
            return ROOM_TAIL_S
        return float(np.percentile(self.tails, 90))

    def update(self, now: float, ref_on: bool, mic_db: float | None) -> bool:
        """mic_db None: the mic is muted and hears nothing of the room to measure by."""
        if ref_on or mic_db is None:
            self.last_ref = now if ref_on else self.last_ref
            self.timing = None
        elif self.was_ref and len(self.quiet) >= 50:
            self.timing = [self.last_ref, float(np.percentile(self.quiet, 20)), 0]
        self.was_ref = ref_on
        t = self.timing
        if t is not None:
            t[2] = t[2] + 1 if mic_db <= t[1] + ROOM_DB else 0
            if t[2] >= 3:
                # Three 10 ms frames in a row at the room's own level: the tail ended at the first.
                self.measured = max(0.0, now - 0.02 - t[0])
                self.tails.append(self.measured)
                self.timing = None
            elif now - t[0] > 1.5:
                self.timing = None    # the room never went quiet (he was talking): no measurement
        # Only the measured tail, never the room's level now: him talking right after it stops
        # is the room staying loud too.
        self.on = now - self.last_ref < self.tail()
        if not self.on and self.timing is None and mic_db is not None:
            self.quiet.append(mic_db)
        return self.on


# ---------------------------------------------------------------- speaker

class Speaker:
    """Keeps hw:0,1 running continuously, writing silence when there is nothing
    to say, so the echo reference never stops and playback starts instantly."""

    def __init__(self):
        self.queue = deque()          # (10 ms frame, says_a_trigger_word, sentence seq or None)
        self.partial = bytearray()
        self.partial_seq = None
        self.complete = set()         # sentences whose audio has all arrived
        self.have = None              # the last sentence held whole, for a resume after a dropped link
        self.boot = None              # which run of the brain the sentence numbers belong to
        self.hold = True              # the jitter buffer is filling: play nothing from the queue yet
        self.need = PREBUFFER_S
        self.rebuffer = REBUFFER_S
        self.dry_at = 0.0
        self.clause_open = False      # a clause's audio is still streaming in
        self.tag = False              # the sentence being queued contains a trigger word
        self.self_trigger_until = 0.0 # its own trigger word is audible until this time
        self.lock = threading.Lock()
        self.volume = 0.7
        self.playing = False
        self.drained = threading.Event()
        self.gain = 1.0               # ducking, applied as it plays so it reaches what is already queued
        self.duck_target = 1.0
        self.overlay = bytearray()    # a chime mixed over speech instead of queued behind it
        self.play_started = 0.0
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-D", "hw:0,1", "-t", "raw", "-f", "S16_LE", "-r", str(OUT_RATE), "-c", "2",
             "--buffer-time=240000", "--period-time=20000"],
            stdin=subprocess.PIPE, bufsize=0,
        )
        self.silence = b"\0" * OUT_FRAME * 4
        threading.Thread(target=self._run, daemon=True).start()

    def play(self, pcm_mono_48k: bytes, final: bool = True, seq=None):
        """Queue audio in whole 10 ms frames. A partial frame waits for the next
        packet rather than being padded, because padding mid-stream is a click;
        only the end of a stream (final) is padded out with silence. seq is the
        brain's sentence the audio belongs to; None is the cube's own sound."""
        a = np.frombuffer(pcm_mono_48k, dtype="<i2").astype(np.float32) * self.volume
        stereo = np.repeat(np.clip(a, -32768, 32767).astype("<i2"), 2).tobytes()
        size = OUT_FRAME * 4
        with self.lock:
            if self.partial and self.partial_seq != seq:
                self.queue.append((bytes(self.partial).ljust(size, b"\0"), self.tag, self.partial_seq))
                self.partial.clear()
            self.partial_seq = seq
            self.partial += stereo
            whole = len(self.partial) - len(self.partial) % size
            for i in range(0, whole, size):
                self.queue.append((bytes(self.partial[i:i + size]), self.tag, seq))
            del self.partial[:whole]
            if final and self.partial:
                self.queue.append((bytes(self.partial).ljust(size, b"\0"), self.tag, seq))
                self.partial.clear()
            self.playing = True
            self.drained.clear()

    def new_boot(self, boot):
        """The brain restarted and numbers its sentences from 1 again: what was whole
        before says nothing about the new ones."""
        with self.lock:
            self.boot, self.have = boot, None
            self.complete.clear()

    def start_clause(self):
        with self.lock:
            self.clause_open = True

    def end_clause(self, seq, sentence: bool = False):
        """All of a clause has arrived (and with sentence, all of sentence seq): what is
        queued may play to its end without waiting."""
        with self.lock:
            if self.partial and self.partial_seq == seq:
                self.queue.append((bytes(self.partial).ljust(OUT_FRAME * 4, b"\0"), self.tag, seq))
                self.partial.clear()
            self.clause_open = False
            if sentence:
                self.complete.add(seq)
                if len(self.complete) > 400:
                    self.complete = set(sorted(self.complete)[-200:])
                self.have = seq if self.have is None else max(self.have, seq)

    def flush(self):
        """The end of what the brain is saying for now: everything queued is whole."""
        self.play(b"", final=True, seq=self.partial_seq)
        with self.lock:
            seqs = {q[2] for q in self.queue if q[2] is not None and q[2] >= 0}
            self.complete.update(seqs)
            if seqs:
                self.have = max(seqs | ({self.have} if self.have is not None else set()))
            self.clause_open = False

    def begin(self):
        """A new reply: start the rebuffering patience over."""
        with self.lock:
            self.rebuffer = REBUFFER_S

    def drop_incomplete(self) -> float:
        """The link dropped: keep the sentences that arrived whole, drop the one it was
        cut off in the middle of. The brain sends that one again from its start."""
        with self.lock:
            keep = deque(q for q in self.queue if q[2] is None or q[2] in self.complete)
            dropped = len(self.queue) - len(keep)
            self.queue = keep
            if self.partial_seq not in self.complete:
                self.partial.clear()
            self.clause_open = False
        return dropped / 100

    def duck(self, on: bool, level: float):
        self.duck_target = level if on else 1.0

    def queued_s(self) -> float:
        with self.lock:
            return len(self.queue) / 100

    def ducked(self) -> bool:
        return self.duck_target < 1.0

    def mix(self, pcm_mono_48k: bytes):
        """Play over whatever is playing now rather than after it."""
        a = np.frombuffer(pcm_mono_48k, dtype="<i2").astype(np.float32) * self.volume
        stereo = np.repeat(np.clip(a, -32768, 32767).astype("<i2"), 2).tobytes()
        with self.lock:
            self.overlay += stereo

    def stop(self, why: str):
        with self.lock:
            left = len(self.queue)
            self.queue.clear()
            self.partial.clear()
            self.hold, self.need, self.clause_open = True, PREBUFFER_S, False
        if left:
            log(f"speaker stopped by {why} with {left / 100:.1f}s unplayed")

    def busy(self) -> bool:
        with self.lock:
            return bool(self.queue)

    def _run(self):
        # Write 40 ms at a time into a 240 ms ALSA buffer: enough slack that the
        # mic thread holding the interpreter never starves the speaker into a click.
        while True:
            with self.lock:
                if self.hold and self.queue:
                    head = self.queue[0][2]
                    # Its own sounds go at once; speech once enough is queued, or once all of the
                    # clause it is in has arrived (Kokoro sends a clause whole).
                    if head is None or len(self.queue) / 100 >= self.need or head in self.complete \
                            or not self.clause_open:
                        if self.dry_at:
                            log(f"speech resumed after {time.monotonic() - self.dry_at:.1f}s waiting on the network")
                            self.dry_at = 0.0
                        self.hold = False
                chunks = [] if self.hold else [self.queue.popleft() for _ in range(min(4, len(self.queue)))]
                if not self.hold and not self.queue:
                    self.hold = True
                    last = chunks[-1][2] if chunks else None
                    if last is not None and self.clause_open and self.partial_seq == last:
                        # Ran dry in the middle of a sentence: wait for more rather than stutter.
                        self.need = self.rebuffer
                        self.rebuffer = min(self.rebuffer * 2, REBUFFER_MAX_S)
                        self.dry_at = time.monotonic()
                        log(f"speech ran dry mid-sentence; waiting for {self.need:.1f}s of it before going on")
                    else:
                        self.need = PREBUFFER_S
            if chunks and not self.play_started:
                self.play_started = time.monotonic()
            if not chunks and self.playing and not self.queue:
                self.playing = False
                self.drained.set()
                if self.play_started:
                    log(f"speaker finished after {time.monotonic() - self.play_started:.1f}s of playing")
                    self.play_started = 0.0
            if any(tagged for _, tagged, _ in chunks):
                # Audible a little after it is written (ALSA buffer), and the wake
                # model decides a little after it hears it: keep a margin.
                self.self_trigger_until = time.monotonic() + 0.9
            out = b"".join(c for c, _, _ in chunks) if chunks else self.silence
            g0, target = self.gain, self.duck_target
            # About a tenth of a second to duck or come back, so neither clicks.
            g1 = g0 + max(-0.25, min(0.25, target - g0)) if abs(target - g0) > 1e-3 else target
            self.gain = g1
            if chunks and (g0 != 1.0 or g1 != 1.0):
                a = np.frombuffer(out, dtype="<i2").astype(np.float32)
                a *= np.repeat(np.linspace(g0, g1, len(a) // 2, dtype=np.float32), 2)
                out = a.astype("<i2").tobytes()
            if self.overlay:
                with self.lock:
                    over, self.overlay = bytes(self.overlay[:len(out)]), self.overlay[len(out):]
                a = np.frombuffer(out, dtype="<i2").astype(np.float32)
                a[:len(over) // 2] += np.frombuffer(over, dtype="<i2").astype(np.float32)
                out = np.clip(a, -32768, 32767).astype("<i2").tobytes()
            self.proc.stdin.write(out)


def chime(speaker: Speaker, up: bool = True, over: bool = False):
    t = np.arange(int(OUT_RATE * 0.07)) / OUT_RATE
    tones = (660, 990) if up else (990, 660)
    parts = []
    for f in tones:
        env = np.sin(np.pi * np.arange(len(t)) / len(t))
        parts.append(0.18 * env * np.sin(2 * np.pi * f * t))
    pcm = (np.concatenate(parts) * 32767).astype("<i2").tobytes()
    if over:
        speaker.mix(pcm)       # over its own speech, which carries on ducked
    else:
        speaker.play(pcm)


# ---------------------------------------------------------------- minimal websocket client

class WebSocket:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.last_rx = time.monotonic()   # anything at all from the brain, pings included

    @classmethod
    async def connect(cls, url: str):
        assert url.startswith("ws://")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port or 80)), 10)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        await writer.drain()
        status = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise ConnectionError(status[:80])
        return cls(reader, writer)

    async def _send(self, opcode: int, payload: bytes):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = (np.frombuffer(payload, np.uint8) ^ np.resize(np.frombuffer(mask, np.uint8), n)).tobytes() if n else b""
        self.w.write(head + mask + body)
        await self.w.drain()

    async def send_json(self, **msg):
        await self._send(0x1, json.dumps(msg).encode())

    async def send_bytes(self, data: bytes):
        await self._send(0x2, data)

    async def recv(self):
        """Return str or bytes for a data message; answers pings itself."""
        buf, kind = b"", None
        while True:
            b1, b2 = await self.r.readexactly(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", await self.r.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", await self.r.readexactly(8))[0]
            payload = await self.r.readexactly(n)
            self.last_rx = time.monotonic()
            if op == 0x9:
                await self._send(0xA, payload)
                continue
            if op == 0x8:
                raise ConnectionError("closed by server")
            if op in (0x1, 0x2):
                kind = op
            buf += payload
            if fin:
                return buf.decode() if kind == 0x1 else buf

    def close(self):
        self.w.close()

    def abort(self):
        self.w.transport.abort()


# ---------------------------------------------------------------- the agent

class Cube:
    def __init__(self):
        self.cfg = load_config()
        self.speaker = Speaker()
        self.speaker.volume = self.cfg["volume"] / 100
        self.aec = EchoCanceller(self.cfg["echo_tail_ms"])
        # One wake model per feed, each fed its own feed all the time, so the one for the quiet
        # room is ready the moment its own voice stops. Switching a single model's feed from the
        # raw cancelled audio to the denoised one left its features adapted to its own voice and
        # deaf to "sunday" right after it spoke. self.wake is the one in use now.
        self.wake_loud = MicroWakeWord.from_config(HOME / "wake" / f"{self.cfg['wake_word']}.json", TFLITE)
        self.wake_quiet = MicroWakeWord.from_config(HOME / "wake" / f"{self.cfg['wake_word']}.json", TFLITE)
        self.wake_loud.probability_cutoff = self.cfg["speaking_wake_cutoff"]
        self.wake_quiet.probability_cutoff = self.cfg["wake_cutoff"]
        self.wake = self.wake_quiet
        # Once its own voice stops, the quiet-room model may still hold a score it gave that voice:
        # it decides nothing until its score has fallen under REARM, so only a new word wakes it.
        self.quiet_armed = True
        self.gain = 10 ** (self.cfg["mic_gain_db"] / 20)
        # The stock "stop" model fires at 0.5, which the cube's own voice can reach
        # through the echo canceller; this copy asks for 0.85.
        self.stop_word = MicroWakeWord.from_config(HOME / "wake" / "stop.json", TFLITE)
        self.features = MicroWakeWordFeatures()          # the raw cancelled feed, for wake_loud and the stop word
        self.features_quiet = MicroWakeWordFeatures()    # the denoised feed, for wake_quiet
        # A third model, started fresh on the denoised feed each time its speaker goes quiet with
        # nothing left to say. A model that heard its own last words scored as its name stays
        # deaf to "sunday" for most of a second after (cube/wake_replay.py); a fresh one does
        # not. It decides only once the room is quiet again, and stops after FRESH_S.
        self.wake_fresh = MicroWakeWord.from_config(HOME / "wake" / f"{self.cfg['wake_word']}.json", TFLITE)
        self.features_fresh = None
        self.fresh_until = 0.0
        self.ref_was = False
        self.wake_model = self.wake_quiet    # the model whose score a wake reports
        self.ws = None
        self.listening = False
        self.speaking = False
        self.awaiting = False
        self.muted = False
        self.computer_playing = False   # one of his computers is playing sound
        self.voice_muted = False        # muted by voice: the brain ignores everything but "unmute"
        self.loop = None
        self.out_pending = bytearray()
        self.cooldown_until = 0.0
        self.stop_cooldown_until = 0.0
        self.duck_since = 0.0
        self.near_peak = 0.0
        self.chime_pending = False     # a doubtful wake's chime, waiting for speech
        self.held_since = None        # a wake heard with no connection, waiting for one
        self.held_audio = []
        self.reconnect = None
        self.echo_stats = [0, 0.0, 0.0, 0.0]
        self.levels = deque(maxlen=100)   # dBFS of the last second of what the wake word hears
        self.wake_level = -90.0
        self.injected = deque()          # 10 ms frames sent in place of the mic, for a test
        self.mixin = deque()             # 10 ms frames mixed into the mic, for a test
        self.probe = None                # what the wake word made of a test's sound
        self.watch_until = 0.0           # for a test: log the feeds' levels and the gain this long
        self.rec = None                  # for a test: the raw four channels being kept, and until when
        self.watch_n = 0
        self.followup_gen = 0            # bumped by a wake or a stop: a follow-up window still waiting is dropped
        self.cur_seq = -1                # the brain's sentence its audio belongs to now
        self.linked = False              # connected and introduced: the brain knows which cube this is
        self.ear_on = False              # the mic is going to the brain while it talks, with no wake word
        self.talked_at = 0.0             # the last moment it was talking
        self.recent = deque(maxlen=PREROLL_FRAMES)   # the last 1.5 s of what the brain would be sent
        self.residual = deque(maxlen=ECHO_FRAMES)    # (playing, residual dB, mic dB) per 10 ms
        self.wake_pcm = b""              # the wake word, as heard
        self.wake_echo = None            # how much of it its own speaker explains
        self.verify_hold = False         # the brain checks the voice first: chime and ring wait for "speech"
        self.words_hold = False          # the brain checks for words first, the same way
        self.held_wake = False           # a wake waiting on that check
        self.duck_pending = False        # a held wake over its own speech: it turns down only once cleared
        self.sound = Sound()             # its own speaker audible in the room, by the loopback

    # ---- mic thread: capture, cancel echo, detect, forward

    def mic_thread(self):
        proc = subprocess.Popen(
            ["arecord", "-q", "-D", "hw:0,4", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "4",
             "--buffer-time=80000", "--period-time=10000"],
            stdout=subprocess.PIPE,
        )
        size = FRAME * 4 * 2
        batch = bytearray()
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                log("capture ended, exiting for a restart")
                os._exit(1)
            x = np.frombuffer(raw, dtype="<i2").reshape(FRAME, 4)
            if self.rec is not None:
                self.rec[1].append(raw)
                if time.monotonic() > self.rec[0]:
                    Path("/tmp/sunday-rec.raw").write_bytes(b"".join(self.rec[1]))
                    log(f"recorded {len(self.rec[1]) / 100:.1f}s of the raw mics and loopback to /tmp/sunday-rec.raw")
                    self.rec = None
            mic = x[:, 0].copy()
            inj = None
            if self.mixin:
                # A test's sound goes in where the room's would, ahead of the echo canceller,
                # so the wake word hears it through the same processing as his voice.
                inj = np.frombuffer(self.mixin.popleft(), "<i2").astype(np.float32)
                mic = np.clip(mic.astype(np.float32) + inj / self.gain, -32768, 32767).astype("<i2")
                if not self.mixin:
                    self.inject_ended = time.monotonic()
                    log("injected speech ended")
            mic = mic.tobytes()
            ref = x[:, 2].copy().tobytes()
            cancelled, processed = self.aec.process(mic, ref)
            self.measure(x[:, 0], x[:, 2], cancelled)
            r = float(np.square(x[:, 2].astype(np.float32)).mean())
            mic_db = 10 * np.log10(float(np.square(x[:, 0].astype(np.float32) - x[:, 0].mean()).mean()) + 1.0)
            talking = self.speaking or self.speaker.busy()
            # Whether its own voice can be heard in the room, by the loopback and the measured
            # echo tail, not by what the brain is doing: the brain lets the Mac's wake word go by
            # it, though a reply may still be on its way.
            was_sounding = self.sound.on
            sounding = self.sound.update(time.monotonic(), r > 1e4, None if self.muted else mic_db)
            if sounding != was_sounding:
                self.loop.call_soon_threadsafe(self.report_sound, sounding)
            if self.sound.measured is not None:
                if not talking:
                    log(f"echo tail {self.sound.measured * 1000:.0f} ms after it played; "
                        f"using {self.sound.tail() * 1000:.0f} ms ({len(self.sound.tails)} measured)")
                self.sound.measured = None
            if self.muted:
                continue
            # The wake word hears the echo-cancelled mic with a fixed gain: the
            # preprocessor's echo suppression and AGC pump its own residual voice up
            # and push his down, which is what made "sunday" miss while it talked.
            loud = np.frombuffer(cancelled, "<i2").astype(np.float32) * self.gain
            # A dry probe is for the wake word alone: the brain is sent the room without it.
            sent = np.clip(loud - inj, -32768, 32767).astype("<i2").tobytes() \
                if inj is not None and self.probe is not None and self.probe.get("dry") else None
            loud = np.clip(loud, -32768, 32767).astype("<i2").tobytes()
            sent = sent or loud
            self.recent.append(loud)
            self.residual.append((r > 1e4, 10 * np.log10(float(np.square(
                np.frombuffer(loud, "<i2").astype(np.float32)).mean()) + 1.0), mic_db))
            if self.watch_until > time.monotonic():
                self.watch_n += 1
                if self.watch_n % 10 == 0:
                    def dbfs(b):
                        a = np.frombuffer(b, "<i2").astype(np.float32)
                        return 10 * np.log10(float(np.square(a).mean()) / 1073741824.0 + 1e-12)
                    log(f"watch: gain {self.aec.agc_gain_db()} dB, loud {dbfs(loud):.0f}, processed "
                        f"{dbfs(processed):.0f} dBFS, ref {'on' if r > 1e4 else 'off'}, sounding {self.sound.on}")
            # The raw cancelled feed decides while it has speech queued, the gaps between words
            # included. The last of it, still in the speaker's buffer, and its echo are left to
            # the denoised feed, whose echo suppression keeps them from waking it: deciding on
            # the raw feed until the room was quiet woke it on its own last word in 3 of 11
            # lines (1 in 102 before). The quiet-room model has heard its own feed all along,
            # and is re-armed only once it no longer scores its own voice (REARM).
            voiced = self.speaker.busy()
            self.wake_loud.probability_cutoff = self.cfg["speaking_wake_cutoff"]
            self.wake_quiet.probability_cutoff = (self.cfg["playing_wake_cutoff"] if self.computer_playing
                                                  else self.cfg["wake_cutoff"])
            self.wake = self.wake_loud if voiced else self.wake_quiet
            # Quiet, the denoised and auto-gained audio is what the wake model hears
            # best; only while its own voice is in the room does the raw cancelled feed beat it.
            heard = loud if voiced else processed
            x = np.frombuffer(heard, "<i2").astype(np.float32)
            self.levels.append(10 * np.log10(float(np.square(x).mean()) / 1073741824.0 + 1e-12))
            probe = self.probe
            if probe is not None:
                probe["db"] = max(probe["db"], self.levels[-1])
                probe["feeds"].add("loud" if voiced else "processed")
            quiet_feats = list(self.features_quiet.process_streaming(processed))
            ref_on = r > 1e4
            if self.ref_was and not ref_on and not voiced:
                self.wake_fresh.reset()
                self.features_fresh = MicroWakeWordFeatures()
                self.fresh_until = time.monotonic() + FRESH_S
            self.ref_was = ref_on
            self.wake_fresh.probability_cutoff = self.wake_quiet.probability_cutoff
            fresh_feats = list(self.features_fresh.process_streaming(processed)) \
                if self.features_fresh is not None and time.monotonic() < self.fresh_until else []
            for i, feats in enumerate(self.features.process_streaming(loud)):
                now = time.monotonic()
                qfeats = quiet_feats[i] if i < len(quiet_feats) else None
                fresh_fired = i < len(fresh_feats) and self.wake_fresh.process_streaming(fresh_feats[i])
                # The model not in use hears its own feed too, and decides nothing.
                if voiced and qfeats is not None:
                    self.wake_quiet.process_streaming(qfeats)
                elif not voiced:
                    self.wake_loud.process_streaming(feats)
                heard_feats = feats if voiced else qfeats
                if heard_feats is None:
                    continue
                if voiced:
                    self.quiet_armed = False
                if now < self.speaker.self_trigger_until:
                    # It is saying "Sunday" or "stop" itself right now; that isn't him.
                    self.wake.process_streaming(heard_feats)
                    self.stop_word.process_streaming(feats)
                    if probe is not None:
                        probe["blocked"] = "its own trigger word"
                    continue
                fired = self.wake.process_streaming(heard_feats)
                if not voiced and not self.quiet_armed:
                    probs = self.wake_quiet._probabilities
                    if (sum(probs) / len(probs) if probs else 0.0) < REARM:
                        self.quiet_armed = True
                    elif fired:
                        log("wake word held: its own voice was still in what the wake word heard")
                        fired = False
                        if probe is not None:
                            probe["blocked"] = "its own voice still in the wake word"
                by_fresh = fresh_fired and not voiced and not self.sound.on and not fired
                fired = fired or by_fresh
                self.track_near_miss(fired, talking)
                if probe is not None:
                    probs = self.wake._probabilities
                    probe["peak"] = max(probe["peak"], sum(probs) / len(probs) if probs else 0.0)
                    if now < self.fresh_until and self.wake_fresh._probabilities:
                        probs = self.wake_fresh._probabilities
                        probe["peak"] = max(probe["peak"], sum(probs) / len(probs))
                        probe["feeds"].add("fresh")
                    probe["fired"] = probe["fired"] or fired
                    if fired and (now <= self.cooldown_until or self.listening):
                        probe["blocked"] = "listening" if self.listening else "cooldown"
                    if now > probe["until"]:
                        # A test's sound: how close the wake word came, whether or not it fired.
                        self.probe = None
                        log(f"{'probe' if probe.get('dry') else 'injected sound'}: wake peak {probe['peak']:.2f} "
                            f"(cutoff {self.wake.probability_cutoff:.2f})"
                            f"{', fired' if probe['fired'] else ''}"
                            f"{' but ' + probe['blocked'] if probe.get('blocked') else ''}, "
                            f"{'+'.join(sorted(probe['feeds']))} feed, loudest {probe['db']:.0f} dBFS"
                            f"{', listening' if self.listening else ''}")
                    elif probe.get("dry"):
                        continue          # a dry probe never wakes or stops it
                if fired and now > self.cooldown_until and not self.listening:
                    self.cooldown_until = now + 1.5
                    self.wake_level = max(self.levels) if self.levels else -90.0
                    self.wake_pcm = b"".join(self.recent)
                    self.wake_echo = self.echo_report()
                    self.wake_model = self.wake_fresh if by_fresh else self.wake
                    if by_fresh:
                        log("wake word heard by the fresh model, after its own voice")
                    self.loop.call_soon_threadsafe(self.on_wake_word)
                # Its own cooldown: "Sunday, stop" said in one breath while it talks must
                # still stop it at once, and a wake no longer silences it by itself.
                if talking and self.stop_word.process_streaming(feats) \
                        and now > self.stop_cooldown_until:
                    self.stop_cooldown_until = now + 1.0
                    self.cooldown_until = max(self.cooldown_until, now + 1.0)
                    self.loop.call_soon_threadsafe(self.on_stop_word)
            now = time.monotonic()
            if talking:
                self.talked_at = now
            # While it talks it is already awake: everything the mic hears goes to the
            # brain, which decides whether he said anything that needs it to stop.
            ear = self.linked and not self.listening and not self.voice_muted and now - self.talked_at < EAR_HANG_S
            if ear != self.ear_on:
                self.ear_on = ear
                batch = bytearray()
                self.loop.call_soon_threadsafe(self.ear_state, ear)
            if self.listening or ear:
                batch += self.injected.popleft() if self.injected and self.listening else sent
                if len(batch) >= FRAME * 2 * 2:   # 20 ms per websocket frame
                    data, batch = bytes(batch), bytearray()
                    self.loop.call_soon_threadsafe(self.forward, data, ear)
            elif batch:
                batch = bytearray()

    def measure(self, mic, ref, clean):
        """While the speaker plays, track how much of it the mics hear before and
        after cancellation, and log the echo return loss enhancement afterwards."""
        r = float(np.square(ref.astype(np.float32)).mean())
        if r > 1e4:
            st = self.echo_stats
            st[0] += 1
            st[1] += r
            st[2] += float(np.square(mic.astype(np.float32) - mic.mean()).mean())
            st[3] += float(np.square(np.frombuffer(clean, "<i2").astype(np.float32)).mean())
        elif self.echo_stats[0] > 50:
            n, r, m, c = self.echo_stats
            log(f"played {n / 100:.1f}s: ref {10 * np.log10(r / n):.0f} dB, mic {10 * np.log10(m / n + 1):.0f} dB, "
                f"after AEC {10 * np.log10(c / n + 1):.0f} dB, ERLE {10 * np.log10((m + 1) / (c + 1)):.1f} dB")
            self.echo_stats = [0, 0.0, 0.0, 0.0]
        elif self.echo_stats[0]:
            self.echo_stats = [0, 0.0, 0.0, 0.0]

    def echo_report(self) -> dict:
        """At a wake: was it playing, and how far above its own voice's usual residual was the
        cancelled mic during the wake word. Its own voice coming through is at about 0 dB."""
        rows = list(self.residual)
        wake, before = rows[-WAKE_FRAMES:], rows[:-WAKE_FRAMES]

        def power_db(xs):
            return float(10 * np.log10(np.mean([10 ** (c / 10) for c in xs]))) if xs else None

        playing = sum(1 for p, _, _ in wake if p) > len(wake) // 2
        out = {"playing": playing, "mic_db": round(power_db([m for _, _, m in wake]) or 0.0, 1),
               "residual_db": round(power_db([c for _, c, _ in wake]) or 0.0, 1)}
        base = [c for p, c, _ in before if p]
        if playing and len(base) >= 2 * WAKE_FRAMES:
            # Its own voice's residual, one second at a time; the loud end of it is the bar.
            seconds = [power_db(base[i:i + WAKE_FRAMES]) for i in range(0, len(base) - WAKE_FRAMES + 1, 25)]
            out["excess_db"] = round(out["residual_db"] - float(np.percentile(seconds, 90)), 1)
        return out

    def forward(self, data: bytes, ear: bool = False):
        if self.ws and (self.ear_on if ear else self.listening):
            asyncio.ensure_future(self._safe(self.ws.send_bytes(data)))
        elif not ear and self.held_since is not None and self.listening:
            self.held_audio.append(data)   # what he says after a held wake, sent on reconnect

    def ear_state(self, on: bool):
        """The open ear starts or stops: the brain reads the audio between as heard over its speech."""
        if self.ws and self.linked:
            asyncio.ensure_future(self._safe(self.ws.send_json(type="ear", on=on)))

    def since_injection(self) -> str:
        """For a test: how long after injected speech ended something happened."""
        if self.mixin:
            return f", {len(self.mixin) * 10} ms before the injected speech ended"
        ago = time.monotonic() - getattr(self, "inject_ended", 0.0)
        return f", {ago * 1000:.0f} ms after the injected speech ended" if ago < 10 else ""

    async def _safe(self, coro):
        try:
            await coro
        except Exception as e:
            log("send failed:", repr(e))

    # ---- events

    def track_near_miss(self, fired: bool, talking: bool):
        """Log a "sunday" the model nearly took, so a missed wake shows how close it came."""
        probs = self.wake._probabilities
        p = sum(probs) / len(probs) if probs else 0.0
        if fired:
            self.near_peak = 0.0
        elif p > self.near_peak:
            self.near_peak = p
        elif p < 0.2 and self.near_peak >= 0.5:
            log(f"near miss {self.near_peak:.2f} (cutoff {self.wake.probability_cutoff:.2f}"
                f"{', talking' if talking else ''}{', listening' if self.listening else ''})")
            self.near_peak = 0.0

    def on_wake_word(self):
        # A clear "sunday" chimes at once. A doubtful one (a rattle, a word like it)
        # lights the ring and listens quietly; the chime waits until the brain hears speech.
        probs = self.wake_model._probabilities
        self.wake_prob = sum(probs) / len(probs) if probs else 0.0
        sure = self.wake_prob >= self.cfg["sure_wake"]
        log(f"wake word {self.wake_prob:.2f} at {self.wake_level:.0f} dBFS"
            + (" (chime and ring wait for words)" if self.words_hold
               else "" if sure else " (unsure, chime waits for speech)"))
        self.start_listening(chime_up=True, chime_now=sure, by_word=True)

    def on_stop_word(self, by="stop word"):
        log(by + self.since_injection())
        self.followup_gen += 1
        self.speaker.stop(by)
        self.unduck(by)
        self.speaking = False
        self.rest_led()
        if self.ws:
            # The brain keeps the speech log, so it needs to know where he cut it off.
            asyncio.ensure_future(self._safe(self.ws.send_json(type="stopped", by=by)))

    def start_listening(self, chime_up=True, chime_now=True, by_word=False):
        if chime_up:
            # He has asked something new: the follow-up window the last reply asked for is moot.
            self.followup_gen += 1
        if not self.ws:
            self.hold_wake(chime_up)
            return
        # Talking: it keeps talking, quieter, while he speaks. The brain decides from his
        # words whether that needs it to stop, and says "duck" off or "stop_audio".
        # The brain checks a wake word for words (and, enforcing, whose voice) before anything
        # chimes, lights or turns down: a cough, a click or a voice that isn't his gets nothing
        # at all. The button is always him.
        self.held_wake = chime_up and by_word and (self.verify_hold or self.words_hold)
        talking = self.speaking or self.speaker.busy()
        self.duck_pending = False
        if talking and self.held_wake:
            self.duck_pending = True
        elif talking:
            self.duck("listening over its own speech")
        else:
            self.speaker.stop("listening")
            self.speaking = False
        self.chime_pending = chime_up and (not chime_now or self.held_wake) and not self.voice_muted
        if chime_up and chime_now and not self.held_wake and not self.voice_muted:
            chime(self.speaker, True, over=talking)
        self.listening = True
        self.listen_since = time.monotonic()
        if not self.held_wake:
            led("listening")
        # Tell the brain whether he said the wake word or this is the follow-up window.
        # The probability and level of the wake let the brain weigh it against the Mac's;
        # the wake word itself and the echo figures let it check whose voice it was.
        pcm, self.wake_pcm = (self.wake_pcm if by_word else b""), b""
        asyncio.ensure_future(self._safe(self.ws.send_json(
            type="wake", word=chime_up, sure=chime_up and chime_now,
            prob=round(getattr(self, "wake_prob", 0.0), 3) if chime_up else None,
            level=round(self.wake_level, 1) if chime_up else None,
            by="word" if by_word else "button" if chime_up else None,
            echo=self.wake_echo if by_word else None,
            preroll=base64.b64encode(pcm).decode() if pcm else None)))
        asyncio.ensure_future(self.listen_watchdog(self.listen_since))

    def hold_wake(self, chime_up=True):
        """No brain to talk to: answer him anyway, keep what he says, and hand it
        over if the connection comes back in time."""
        log("wake held: not connected")
        self.speaker.stop("listening")
        self.speaking = False
        if chime_up:
            chime(self.speaker, True)
        since = self.held_since = time.monotonic()
        self.held_audio = []
        self.listening = True
        led("listening")
        if self.reconnect:
            self.reconnect.set()
        asyncio.ensure_future(self.held_watchdog(since))

    async def held_watchdog(self, since):
        await asyncio.sleep(HOLD_WAKE_S)
        if self.held_since == since:
            log("held wake dropped: still not connected")
            self.held_since = None
            self.held_audio = []
            self.listening = False
            led("off")

    async def release_held_wake(self):
        """Connected again with a wake waiting: open the mic on the brain and send
        what was said meanwhile, then let the stream carry on as normal."""
        if self.held_since is None:
            return
        log(f"held wake released after {time.monotonic() - self.held_since:.1f}s")
        audio, self.held_audio, self.held_since = self.held_audio, [], None
        self.listen_since = time.monotonic()
        await self.ws.send_json(type="wake")
        for data in audio:
            await self.ws.send_bytes(data)
        asyncio.ensure_future(self.listen_watchdog(self.listen_since))

    def duck(self, why: str):
        if not self.speaker.ducked():
            log(f"ducked: {why}, {self.speaker.queued_s():.1f}s queued")
        self.speaker.duck(True, self.cfg["duck_volume"])
        self.duck_since = time.monotonic()

    def unduck(self, why: str):
        if self.speaker.ducked():
            log(f"full volume: {why}, {self.speaker.queued_s():.1f}s queued")
        self.speaker.duck(False, 1.0)

    async def duck_watchdog(self):
        """Never stay ducked once nothing is playing, or once the brain has gone quiet."""
        while True:
            await asyncio.sleep(0.5)
            if not self.speaker.ducked():
                continue
            if self.listening:
                self.duck_since = time.monotonic()
            elif not (self.speaking or self.speaker.busy()):
                self.unduck("nothing playing")
            elif time.monotonic() - self.duck_since > DUCK_LIMIT_S:
                self.unduck("no decision from the brain")

    async def listen_watchdog(self, since):
        await asyncio.sleep(75)        # longer than the brain's own utterance limit
        if self.listening and self.listen_since == since:
            log("no answer from the server while listening, giving up")
            self.listening = False
            self.rest_led()

    def on_key(self, key: str):
        log("key", key)
        if key in ("Home", "Tap"):
            if self.speaking or self.speaker.busy():
                self.on_stop_word("stop button")
            else:
                self.start_listening()
        elif key in ("Volup", "Voldown"):
            step = 10 if key == "Volup" else -10
            self.cfg["volume"] = max(10, min(100, self.cfg["volume"] + step))
            self.speaker.volume = self.cfg["volume"] / 100
            save_config(self.cfg)
            chime(self.speaker, key == "Volup")
        elif key == "Mute":
            if self.voice_muted and not self.muted:
                # Muted by voice: the button is the way back.
                asyncio.ensure_future(self._safe(self.ws.send_json(type="unmute", by="button")))
                self.voice_muted = False
                self.rest_led()
            else:
                self.set_muted(not self.muted)

    def rest_led(self):
        led("muted" if (self.muted or self.voice_muted) else "idle")

    def set_muted(self, muted: bool):
        self.muted = muted
        try:
            Path(MIC_GPIO).write_text("0" if muted else "1")
        except OSError:
            pass
        if muted:
            self.listening = False
            self.held_since = None
            self.held_audio = []
        self.rest_led()
        # Muted by the button, the cube hears nothing, so the Mac answers instead.
        if self.ws:
            asyncio.ensure_future(self._safe(self.ws.send_json(type="hw_mute", muted=muted)))

    # ---- server messages

    async def handle(self, msg):
        if isinstance(msg, bytes):
            pcm = self.out_pending + msg
            even = len(pcm) - len(pcm) % 2
            self.speaker.play(bytes(pcm[:even]), final=False, seq=self.cur_seq)
            self.out_pending = bytearray(pcm[even:])
            return
        m = json.loads(msg)
        t = m.get("type")
        if t == "speech":
            # He is talking: a wake that was waiting to be sure gets its chime now. A held
            # wake is his voice: it chimes and lights now, or lights for the answer if he
            # has already finished.
            if self.chime_pending and self.listening and not self.voice_muted:
                chime(self.speaker, True, over=self.speaking or self.speaker.busy())
            self.chime_pending = False
            if self.held_wake:
                self.held_wake = False
                log("speech: words, " + ("held for the voice check" if self.verify_hold else "not a sound"))
                if not (self.speaking or self.speaker.busy()):
                    led("listening" if self.listening else "thinking")
            if self.duck_pending:
                self.duck_pending = False
                if self.speaking or self.speaker.busy():
                    self.duck("listening over its own speech")
        elif t == "verify":
            if bool(m.get("hold")) != self.verify_hold:
                log("wakes wait for the voice check" if m.get("hold") else "wakes chime at once")
            self.verify_hold = bool(m.get("hold"))
            if bool(m.get("words")) != self.words_hold:
                log("wakes wait for words: no chime, ring or turning down for a sound" if m.get("words")
                    else "wakes no longer wait for words")
            self.words_hold = bool(m.get("words"))
        elif t == "standdown":
            # The Mac already has this one: drop it without a sound and put the ring back.
            log("stood down:", m.get("why", ""))
            self.chime_pending = False
            self.held_wake = False
            self.duck_pending = False
            self.listening = False
            self.unduck("stood down")
            if not (self.speaking or self.speaker.busy()):
                self.rest_led()
        elif t == "stop_listening":
            self.chime_pending = False
            self.listening = False
            self.injected.clear()
            if not self.held_wake:
                led("speaking" if (self.speaking or self.speaker.busy()) else "thinking")
                asyncio.ensure_future(self.thinking_watchdog())
        elif t == "idle":
            self.held_wake = False
            self.duck_pending = False
            if not (self.listening or self.speaking or self.speaker.busy()):
                self.rest_led()
        elif t == "sentence":
            words = m.get("text", "").lower()
            self.speaker.tag = bool(TRIGGER_WORDS.search(words))
            self.cur_seq = m.get("seq", -1)
            if m.get("boot") and m["boot"] != self.speaker.boot:
                self.speaker.new_boot(m["boot"])
            self.speaker.start_clause()
        elif t == "clause_end":
            self.speaker.end_clause(m.get("seq"))
        elif t == "sentence_end":
            self.speaker.end_clause(m.get("seq"), sentence=True)
        elif t == "speak_start":
            if not self.speaking:
                self.speaker.begin()
            self.speaking = True
            led("speaking")
        elif t == "speak_end":
            self.speaker.flush()
            asyncio.ensure_future(self.finish_speaking())
        elif t == "stop_audio":
            self.followup_gen += 1
            self.speaker.stop("server" + self.since_injection())
            self.unduck("stopped")
            self.speaking = False
        elif t == "duck":
            if m.get("on"):
                self.duck(m.get("why") or "he is talking to another device")
            else:
                self.unduck("the brain says it carries on" + self.since_injection())
        elif t == "mute":
            self.voice_muted = bool(m.get("muted"))
            log("voice mute", "on" if self.voice_muted else "off")
            if not (self.listening or self.speaking or self.speaker.busy()):
                self.rest_led()
        elif t == "computer_audio":
            if bool(m.get("playing")) != self.computer_playing:
                log("computer audio", "playing" if m.get("playing") else "quiet")
            self.computer_playing = bool(m.get("playing"))
        elif t == "listen":
            asyncio.ensure_future(self.listen_after_reply())
        elif t == "error":
            log("server error:", m.get("text"))
            self.rest_led()
            self.listening = False

    async def thinking_watchdog(self):
        """Never leave the ring on "thinking" if the server goes quiet."""
        await asyncio.sleep(180)
        if _led_state == "thinking" and not (self.listening or self.speaking or self.speaker.busy()):
            self.rest_led()

    async def finish_speaking(self):
        while self.speaker.busy():
            await asyncio.sleep(0.05)
        self.speaking = False
        if not self.listening:
            self.rest_led()

    async def listen_after_reply(self):
        """The reply asked a question: keep the mic open once it has finished talking."""
        gen = self.followup_gen
        while self.speaker.busy() or self.speaking:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.15)
        if gen != self.followup_gen or self.listening:
            log("follow-up window dropped: he spoke over the reply or stopped it")
            return
        self.start_listening(chime_up=False)

    # ---- connections

    async def keys(self):
        if not os.path.exists(KEY_FIFO):
            os.mkfifo(KEY_FIFO)
        while True:
            line = await asyncio.to_thread(lambda: open(KEY_FIFO).readline())
            if line.strip():
                self.on_key(line.split()[0])

    async def inject(self):
        if not os.path.exists(INJECT_FIFO):
            os.mkfifo(INJECT_FIFO)
        while True:
            line = (await asyncio.to_thread(lambda: open(INJECT_FIFO).readline())).split()
            if len(line) in (2, 3) and line[0] in ("hear", "probe"):
                # "probe" is "hear" for the wake word alone: it reports how close the wake word
                # came, and never wakes it, stops it or reaches the brain.
                try:
                    pcm = Path(line[1]).read_bytes()
                    gain = 10 ** (float(line[2]) / 20) if len(line) == 3 else 1.0
                except (OSError, ValueError) as e:
                    log("inject:", e)
                    continue
                a = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float32) * gain
                a = np.clip(a, -32768, 32767).astype("<i2").tobytes()
                self.probe = {"until": time.monotonic() + len(a) / (RATE * 2) + 1.0, "peak": 0.0, "fired": False,
                              "feeds": set(), "db": -120.0, "dry": line[0] == "probe"}
                self.mixin.clear()
                self.mixin.extend(a[i:i + FRAME * 2] for i in range(0, len(a) - FRAME * 2 + 1, FRAME * 2))
                log(f"injected {len(a) / (RATE * 2):.2f}s of sound into the mic, no wake word"
                    + (", as a dry probe" if line[0] == "probe" else ""))
                continue
            if len(line) == 2 and line[0] == "record":
                # For a test: keep this many seconds of the raw mics and loopback, to replay offline.
                self.rec = (time.monotonic() + float(line[1]), [])
                continue
            if len(line) == 2 and line[0] == "watch":
                # For a test: the gain and both feeds' levels every 100 ms for this many seconds.
                self.watch_until = time.monotonic() + float(line[1])
                continue
            if len(line) == 2 and line[0] == "wake":
                try:
                    pcm = Path(line[1]).read_bytes() + b"\0" * RATE * 2 * 3
                except OSError as e:
                    log("inject:", e)
                    continue
                self.injected.clear()
                self.injected.extend(pcm[i:i + FRAME * 2] for i in range(0, len(pcm) - FRAME * 2 + 1, FRAME * 2))
                self.wake_prob, self.wake_level = 1.0, -30.0
                self.wake_pcm, self.wake_echo = b"", None   # the file is all of it: checked like a heard wake
                log(f"injected wake with {len(pcm) / (RATE * 2):.1f}s of audio")
                self.start_listening(chime_up=True, chime_now=True, by_word=True)

    async def link_watch(self, ws):
        """A link that has gone quiet is given up at once, so the reconnect (and the
        brain's resend of what was cut off) happens in seconds, not when TCP gives up."""
        while True:
            await asyncio.sleep(1)
            silent = time.monotonic() - ws.last_rx
            if silent > LINK_SILENT_S:
                log(f"nothing from the brain for {silent:.0f}s, reconnecting")
                ws.abort()
                return

    async def connect_forever(self):
        delay = 1
        watch = None
        while True:
            try:
                self.ws = await WebSocket.connect(self.cfg["server"])
                watch = asyncio.ensure_future(self.link_watch(self.ws))
                resume = {"boot": self.speaker.boot, "have": self.speaker.have} if self.speaker.boot else None
                await self.ws.send_json(type="hello", device=os.uname().nodename, hw_muted=self.muted, resume=resume)
                await self.ws.send_json(type="light", on=_led_state in LIT)
                await self.ws.send_json(type="playing", on=self.sound.on, tail_ms=round(self.sound.tail() * 1000))
                self.linked = True
                log("connected to", self.cfg["server"])
                delay = 1
                if self.held_since is not None:
                    await self.release_held_wake()
                else:
                    self.rest_led()
                while True:
                    await self.handle(await self.ws.recv())
            except (OSError, ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
                log("connection lost:", e)
            self.linked = False
            if watch:
                watch.cancel()
                watch = None
            if self.ws:
                self.ws.close()
                dropped = self.speaker.drop_incomplete()
                self.out_pending = bytearray()
                if dropped:
                    log(f"dropped {dropped:.1f}s of a sentence cut off by the link; the brain sends it again")
            self.ws = None
            if self.held_since is None:
                self.listening = False
                led("off")
            # A held wake retries every half second rather than waiting out the backoff.
            self.reconnect = asyncio.Event()
            try:
                await asyncio.wait_for(self.reconnect.wait(), 0.5 if self.held_since is not None else delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 30)

    def report_light(self, on: bool):
        if self.ws:
            asyncio.ensure_future(self._safe(self.ws.send_json(type="light", on=on)))

    def report_sound(self, on: bool):
        """Its speaker started or stopped being heard in the room: the brain lets the Mac's
        wake word go by this, not by when the playback should have ended by its own clock."""
        if self.ws and self.linked:
            asyncio.ensure_future(self._safe(self.ws.send_json(
                type="playing", on=on, tail_ms=round(self.sound.tail() * 1000))))

    async def run(self):
        global _led_listener
        self.loop = asyncio.get_running_loop()
        _led_listener = self.report_light
        self.set_muted(False)
        threading.Thread(target=self.mic_thread, daemon=True).start()
        await asyncio.gather(self.connect_forever(), self.keys(), self.duck_watchdog(), self.inject())


if __name__ == "__main__":
    asyncio.run(Cube().run())
