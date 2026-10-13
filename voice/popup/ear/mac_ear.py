#!/usr/bin/env python3
"""The Mac's ear for Sunday.

Listens for "sunday" on the Mac's built-in microphone with the same microWakeWord
model as the cube. Audio stays on the Mac until a wake has been cleared by the
brain. The cube has priority: a wake heard here is only reported, and the brain
waits a moment for the cube. If the cube heard it too, this side drops it without
a sound. If the cube did not (it is muted, unreachable, or he is away from home),
the brain says "go", and only then does the Mac chime and send the wake word itself (for
the brain's check of whose voice it is) and what he said since the wake, held here
meanwhile, followed by the live stream. The reply plays here. When the brain is enforcing
that check, "go" says hold, and the ding waits for the brain's "speech": a voice that is
not his gets no ding at all.
A wake while it is talking doesn't cut the reply: it plays on, ducked, until the
brain has decided from his words whether he wanted it to stop. While a reply plays it
is already awake: the voice-processed mic goes to the brain the whole time, with no
wake word and no ding, between {"type":"ear"} on and off, and the brain stops it only
when he plainly wants it to.

The audio, and the playback, are the ear-audio helper's job. It sends the microphone
twice: through macOS voice processing, which cancels what this Mac plays, and plain.
Voice processing also strips his voice from across the room to scraps nothing can read
(2026-10-13: three "Sunday, what time is it?" from his usual spot, none woke it, while the
plain microphone heard every word), so the ear listens to the plain microphone, turned up,
and to the voice-processed one only while sound plays here: Sunday's reply and a second
after it, or anything else this Mac plays.

Sunday.app starts this at login:  mac_ear.py <ear-audio helper> <wake model dir>
"""

import asyncio
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import websockets
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

HELPER = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "native" / "ear-audio")
WAKE_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent / "wake")
SERVER = os.environ.get("SUNDAY_EAR_SERVER", "ws://127.0.0.1:18794")   # Sunday.app's tunnel to the Pi
DEVICE = os.environ.get("SUNDAY_EAR_DEVICE", "mac")
# This Mac as its popup names it to the brain, which says which computers are playing sound.
MACHINE = os.environ.get("SUNDAY_EAR_MACHINE") or re.sub(r"\.local$", "", socket.gethostname())

RATE = 16000
FRAME_BYTES = 320            # 10 ms
OUT_RATE = 48000
WAKE_CUTOFF = float(os.environ.get("SUNDAY_EAR_CUTOFF", "0.6"))
# While a computer plays sound. His own "Sunday" at the Mac scores 0.62 to 0.88, so a higher bar
# here leaves him unheard whenever the desktop plays a game or a video; what that sound wakes it
# with the brain keeps silent until his words are heard (README, Words, not sounds).
PLAYING_CUTOFF = float(os.environ.get("SUNDAY_EAR_PLAYING_CUTOFF", "0.6"))
SPEAKING_CUTOFF = float(os.environ.get("SUNDAY_EAR_SPEAKING_CUTOFF", "0.85"))  # while it plays a reply
SURE_WAKE = float(os.environ.get("SUNDAY_EAR_SURE", "0.95"))
PENDING_LIMIT = 4.0          # a reported wake with no verdict by then is dropped
DUCK_VOLUME = float(os.environ.get("SUNDAY_EAR_DUCK", "0.35"))   # its reply while he talks over it
DUCK_LIMIT_S = 45            # ducked with no word from the brain this long after he stopped: full volume again
PREROLL_S = 6.0              # held while a wake waits for its verdict
WAKE_WORD_S = 1.5            # the wake word itself, sent once cleared for the brain's check of whose voice it is
# What it heard before the wake word fired, sent at the head of what follows it: "Sunday, what
# time is it?" said in one breath across the room is at "is it" when the wake word fires, and
# the brain skips the first 0.2 s it is sent as the click of a mic opening.
LEAD_S = 0.8
EAR_HANG_S = 1.0             # the open ear stays open this long after a reply ends, for a sentence he began over it
FOLLOWUP_WAIT_S = 6.0        # a listen still open when a reply ends: its follow-up window waits this long for it to close
# The plain microphone: his "Sunday, what time is it?" from across the room peaks 20 to 25 dB
# over the room, about -40 dBFS, under what the brain's speech detector reliably calls speech.
PLAIN_GAIN = 10 ** (float(os.environ.get("SUNDAY_EAR_PLAIN_GAIN_DB", "12")) / 20)
PLAIN_CEILING = 0.5          # -6 dBFS: louder than this is turned down for the moment, not clipped
PLAIN_RELEASE = 10 ** (6 / 20 / 50)   # and back up at 6 dB a second
PLAIN_HANG_S = 1.0           # voice-processed until this long after a reply ends: the room's echo of it
PLAIN_STALE_S = 0.5          # no plain audio this long: the voice-processed one, as before
# Its own ding reads as this Mac playing sound for about two seconds (the meter cannot leave out
# an afplay started after it), which would put the rest of what he says through voice processing.
DING_HOLD_S = 3.0
# The wake word hears the plain microphone turned down until the room sits this low. The model
# knows the cube's feed, denoised and quiet between words; with the room at the plain
# microphone's own -65 dBFS, his three "Sunday, what time is it?" from across the room
# (2026-10-13 12:41) woke it none of the times, and with the room put at -92 all three. His
# coughs and two televisions, run the same way, woke it 14 times in 20 against 10: each is one
# the brain drops without a sound. Set from the room's level, not fixed, so a new input volume
# moves both alike.
WAKE_ROOM_DB = float(os.environ.get("SUNDAY_EAR_WAKE_ROOM_DB", "-92"))
TRIGGER_WORDS = re.compile(r"\b(sunday|stop)\b")


def _profile_voice() -> dict:
    """The `voice` block of the owner's profile (tools/mtoa_config.py), read directly:
    this file ships inside Sunday.app without the reader beside it."""
    home = Path.home()
    explicit = os.environ.get("MTOA_CONFIG")
    for p in ([Path(explicit)] if explicit else
              [home / ".claude" / "profile" / "mtoa.json", home / ".config" / "mtoa" / "mtoa.json"]):
        try:
            return json.loads(p.read_text()).get("voice") or {}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


# Home is the network the profile names (voice.home_ssid, voice.home_gateways). macOS
# hides the network name from processes without Location access, so the router's
# address is the test, with the name used whenever it is readable. With neither set,
# the Mac counts as home, and the cube, if there is one, keeps its priority.
_VOICE = _profile_voice()
HOME_SSID = os.environ.get("SUNDAY_HOME_SSID", _VOICE.get("home_ssid") or "")
HOME_GATEWAYS = {g for g in os.environ.get("SUNDAY_HOME_GATEWAYS",
                                           ",".join(_VOICE.get("home_gateways") or [])).lower().split(",") if g}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def chime_pcm(up: bool = True) -> bytes:
    t = np.arange(int(OUT_RATE * 0.07)) / OUT_RATE
    parts = []
    for f in ((660, 990) if up else (990, 660)):
        env = np.sin(np.pi * np.arange(len(t)) / len(t))
        parts.append(0.18 * env * np.sin(2 * np.pi * f * t))
    return (np.concatenate(parts) * 32767).astype("<i2").tobytes()


DING = os.path.expanduser("~/Library/Application Support/Sunday/ding.wav")
dinged_at = 0.0


def ding():
    """The listening ding, played with afplay. Through the voice-processing output it
    was too quiet to hear. The computer-audio meter hears it anyway (DING_HOLD_S)."""
    global dinged_at
    dinged_at = time.monotonic()
    try:
        if not os.path.exists(DING):
            import wave
            pcm = np.frombuffer(chime_pcm(True), "<i2").astype(np.float32) * (0.45 / 0.18)
            os.makedirs(os.path.dirname(DING), exist_ok=True)
            with wave.open(DING, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(OUT_RATE)
                w.writeframes(np.clip(pcm, -32768, 32767).astype("<i2").tobytes())
        subprocess.Popen(["afplay", DING], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log("ding failed:", e)


def at_home() -> bool:
    """On the home network. Read every half minute; cheap and needs no permission."""
    if not HOME_SSID and not HOME_GATEWAYS:
        return True
    try:
        out = subprocess.run(["ipconfig", "getsummary", "en0"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"^\s*SSID : (.+)$", out, re.M)
        if m and m.group(1).strip() != "<redacted>":
            return m.group(1).strip() == HOME_SSID
        # The Wi-Fi's own router, not the default route, which a VPN takes over.
        gw = subprocess.run(["ipconfig", "getoption", "en0", "router"], capture_output=True, text=True,
                            timeout=3).stdout.strip()
        if not gw:
            return False
        arp = subprocess.run(["arp", "-n", gw], capture_output=True, text=True, timeout=3).stdout
        mac = re.search(r" at ([0-9a-f:]+) ", arp)
        if not mac:
            return False
        # arp drops leading zeros ("2c:cf:67:be:68:4"); compare normalized.
        norm = lambda s: ":".join(p.zfill(2) for p in s.lower().split(":"))
        return norm(mac.group(1)) in {norm(g) for g in HOME_GATEWAYS}
    except (OSError, subprocess.SubprocessError):
        return False


class Audio:
    """The ear-audio helper: microphone frames in, reply audio out."""

    def __init__(self, on_audio, on_event):
        self.proc = subprocess.Popen([HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.on_audio, self.on_event = on_audio, on_event
        self.wlock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        r = self.proc.stdout
        while True:
            head = r.read(5)
            if len(head) < 5:
                break
            n = struct.unpack("<I", head[1:])[0]
            body = b""
            while len(body) < n:
                chunk = r.read(n - len(body))
                if not chunk:
                    break
                body += chunk
            if head[0] == ord("A"):
                self.on_audio(body, False)
            elif head[0] == ord("R"):
                self.on_audio(body, True)
            elif head[0] == ord("E"):
                try:
                    self.on_event(json.loads(body))
                except ValueError:
                    pass
        log("audio helper ended, exiting for a restart")
        os._exit(1)

    def _send(self, kind: bytes, payload: bytes = b""):
        with self.wlock:
            try:
                self.proc.stdin.write(kind + struct.pack("<I", len(payload)) + payload)
            except OSError:
                pass

    def play(self, pcm: bytes):
        self._send(b"P", pcm)

    def finish(self):
        self._send(b"F")

    def stop(self):
        self._send(b"S")

    def volume(self, v: float):
        self._send(b"V", struct.pack("<f", v))


class Ear:
    def __init__(self):
        self.wake = MicroWakeWord.from_config(WAKE_DIR / "sunday.json")
        self.wake.probability_cutoff = WAKE_CUTOFF
        # One front end per microphone, each fed every frame of its own: the front end learns its
        # stream's room level, and one that had learned the plain microphone's room at -92 dBFS
        # took the voice-processed residual of Sunday's own reply for speech and woke on it (four
        # times in one minute, 2026-10-13 12:50).
        self.features = MicroWakeWordFeatures()
        self.vp_features = MicroWakeWordFeatures()
        self.loop = None
        self.ws = None
        self.state = "idle"            # idle | pending | listening
        self.pending_since = 0.0
        self.held = bytearray()        # audio since a pending wake, sent if it is cleared
        self.sure = False
        self.chime_pending = False
        self.speaking = False          # the brain is sending a reply
        self.playing_until = 0.0       # when what has been sent to the speaker should have played
        self.self_trigger_until = 0.0  # its own "Sunday" or "stop" is audible until then
        self.tag = False
        self.voice_muted = False
        self.computer_playing = False
        self.cooldown_until = 0.0
        self.near_peak = 0.0
        self.levels = deque(maxlen=100)    # dBFS of the last second, 10 ms per entry
        self.floor = -60.0
        self.away = None
        self.sent_bytes = 0            # mic audio that has left the Mac, for the log
        self.drained = asyncio.Event()
        self.drained.set()
        self.listen_since = 0.0
        self.ducked = False
        self.duck_since = 0.0
        self.followup_gen = 0          # bumped by a wake while quiet, his words taken, or a stop: a follow-up window still waiting is dropped
        self.audio = None
        self.lock = threading.Lock()   # the state and the held audio, shared with the mic thread
        self.ear_on = False            # the mic is going to the brain while a reply plays, with no wake word
        self.talked_at = 0.0           # the last moment a reply was playing
        self.recent = bytearray()      # the last WAKE_WORD_S of the mic, kept on the Mac
        self.wake_pcm = b""            # the wake word, as heard
        self.mac_playing = False       # this Mac plays sound, by its popup's meter, as the brain relays it
        self.plain_at = 0.0            # the last plain microphone audio
        self.plain_gain = PLAIN_GAIN
        self.on_plain = None           # which microphone it listens to, for the log
        self.room = None               # the plain microphone's room level, dBFS, for the wake word

    # ---- the microphone (reader thread)

    def plain_wanted(self, now: float) -> str:
        """Why the voice-processed microphone is the one to hear now, or "" for the plain one."""
        if now - self.plain_at > PLAIN_STALE_S:
            return "no plain microphone"
        if self.speaking or now < self.playing_until or now - self.talked_at < PLAIN_HANG_S:
            return "Sunday is talking here"
        if self.mac_playing and now - dinged_at > DING_HOLD_S:
            return "this Mac is playing sound"
        return ""

    def turn_up(self, pcm: bytes) -> bytes:
        """The plain microphone, turned up by PLAIN_GAIN, and down at once for anything loud."""
        x = np.frombuffer(pcm, "<i2").astype(np.float32)
        peak = float(np.abs(x).max()) / 32768 if len(x) else 0.0
        want = min(PLAIN_GAIN, PLAIN_CEILING / peak) if peak > 0 else PLAIN_GAIN
        self.plain_gain = want if want < self.plain_gain else min(want, self.plain_gain * PLAIN_RELEASE)
        return np.clip(x * self.plain_gain, -32768, 32767).astype("<i2").tobytes()

    def for_wake(self, pcm: bytes) -> bytes:
        """The plain microphone as the wake word hears it: turned down so the room sits at
        WAKE_ROOM_DB. The room is a slow floor over 10 ms pieces: falls fast, rises slowly."""
        x = np.frombuffer(pcm, "<i2").astype(np.float32)
        out = np.empty_like(x)
        for i in range(0, len(x), 160):
            c = x[i:i + 160]
            db = max(-100.0, 10 * np.log10(float(np.square(c).mean()) / (32768 ** 2) + 1e-12))
            self.room = db if self.room is None or db < self.room else self.room + 0.002 * (db - self.room)
            out[i:i + 160] = c * 10 ** (min(0.0, max(-40.0, WAKE_ROOM_DB - self.room)) / 20)
        return np.clip(out, -32768, 32767).astype("<i2").tobytes()

    def on_audio(self, pcm: bytes, plain: bool):
        now = time.monotonic()
        if plain:
            self.plain_at = now
            feats_in = list(self.features.process_streaming(self.for_wake(pcm)))
        else:
            feats_in = list(self.vp_features.process_streaming(pcm))
        why = self.plain_wanted(now)
        if plain != (not why):
            return
        if plain != self.on_plain:
            self.on_plain = plain
            log("mic: plain" if plain else f"mic: voice-processed ({why})")
        if plain:
            pcm = self.turn_up(pcm)
        x = np.frombuffer(pcm, "<i2").astype(np.float32)
        if len(x):
            db = 10 * np.log10(float(np.square(x).mean()) / (32768 ** 2) + 1e-12)
            self.levels.append(db)
            # A slow noise floor: falls fast, rises slowly.
            self.floor = db if db < self.floor else self.floor + 0.002 * (db - self.floor)
        self.recent += pcm
        if len(self.recent) > WAKE_WORD_S * RATE * 2:
            del self.recent[: len(self.recent) - int(WAKE_WORD_S * RATE * 2)]
        talking = self.speaking or time.monotonic() < self.playing_until
        self.wake.probability_cutoff = (SPEAKING_CUTOFF if talking
                                        else PLAYING_CUTOFF if self.computer_playing else WAKE_CUTOFF)
        for feats in feats_in:
            now = time.monotonic()
            if now < self.self_trigger_until:
                self.wake.process_streaming(feats)
                continue
            fired = self.wake.process_streaming(feats)
            self.track_near_miss(fired)
            if fired and now > self.cooldown_until and self.state == "idle":
                self.cooldown_until = now + 1.5
                probs = self.wake._probabilities
                p = sum(probs) / len(probs) if probs else 0.0
                level = max(self.levels) if self.levels else -90.0
                with self.lock:
                    self.state = "pending"
                    self.pending_since = now
                    # This frame is already in recent, and joins held below with the rest.
                    before = self.recent[:len(self.recent) - len(pcm)]
                    self.held = bytearray(before[-int(LEAD_S * RATE) * 2:])
                    self.wake_pcm = bytes(self.recent)
                self.loop.call_soon_threadsafe(self.on_wake_word, p, level)
        with self.lock:
            if self.state == "pending":
                self.held += pcm
                if len(self.held) > PREROLL_S * RATE * 2:
                    del self.held[: len(self.held) - int(PREROLL_S * RATE * 2)]
            elif self.state == "listening":
                self.loop.call_soon_threadsafe(self.forward, pcm)
            # While a reply plays it is already awake: the mic goes to the brain, which
            # decides whether he said anything that needs it to stop.
            now = time.monotonic()
            if talking:
                self.talked_at = now
            ear = self.ws is not None and self.state == "idle" and not self.voice_muted \
                and now - self.talked_at < EAR_HANG_S
            if ear != self.ear_on:
                self.ear_on = ear
                self.loop.call_soon_threadsafe(self.ear_state, ear)
            if ear:
                self.loop.call_soon_threadsafe(self.forward_ear, pcm)

    def track_near_miss(self, fired: bool):
        probs = self.wake._probabilities
        p = sum(probs) / len(probs) if probs else 0.0
        if fired:
            self.near_peak = 0.0
        elif p > self.near_peak:
            self.near_peak = p
        elif p < 0.2 and self.near_peak >= 0.5:
            log(f"near miss {self.near_peak:.2f} (cutoff {self.wake.probability_cutoff:.2f})")
            self.near_peak = 0.0

    def on_helper_event(self, ev: dict):
        kind = ev.get("event")
        if kind == "drained":
            self.loop.call_soon_threadsafe(self.drained.set)
        elif kind in ("started", "restarted", "plain started", "asking", "error"):
            log("audio:", json.dumps(ev))

    # ---- wakes

    def on_wake_word(self, p: float, level: float):
        self.sure = p >= SURE_WAKE
        if not self.ws:
            log(f"wake word {p:.2f}, not connected: dropped")
            self.state = "idle"
            return
        log(f"wake word {p:.2f} at {level:.0f} dBFS (floor {self.floor:.0f}): asking the brain")
        asyncio.ensure_future(self._safe(self.ws.send(json.dumps(
            {"type": "wake", "word": True, "sure": self.sure, "prob": round(p, 3),
             "level": round(level, 1), "floor": round(self.floor, 1), "lead": LEAD_S}))))
        asyncio.ensure_future(self.pending_watchdog(self.pending_since))

    async def pending_watchdog(self, since):
        await asyncio.sleep(PENDING_LIMIT)
        if self.state == "pending" and self.pending_since == since:
            log("no verdict on the wake, dropped")
            self.state = "idle"
            self.held = bytearray()

    async def go(self, hold: bool = False):
        """The brain cleared the wake: chime, then send what was held and keep streaming.
        hold: the brain checks whose voice it is first, and says "speech" once it is his;
        the ding waits for that, and a voice that isn't his never gets one."""
        if self.state != "pending":
            return
        waited = time.monotonic() - self.pending_since
        if self.speaking or time.monotonic() < self.playing_until:
            # Talking: carry on, quieter, while he speaks. The brain decides from his words. The
            # follow-up window after the reply stays, unless his words are taken ("speech") or
            # the reply is stopped: most wakes here are its own voice, which the brain lets go.
            self.duck(True, "listening over its own reply")
        else:
            self.followup_gen += 1
        self.chime_pending = (not self.sure or hold) and not self.voice_muted
        if self.sure and not hold and not self.voice_muted:
            ding()
        with self.lock:
            held, self.held = bytes(self.held), bytearray()
            self.state = "listening"
            word, self.wake_pcm = self.wake_pcm, b""
        self.listen_since = time.monotonic()
        self.sent_bytes = 0
        log(f"cleared after {waited * 1000:.0f} ms; sending {len(held)} bytes held since the wake"
            + (", the ding waits for the voice check" if hold else ""))
        if word:
            asyncio.ensure_future(self._safe(self.ws.send(json.dumps(
                {"type": "preroll", "pcm": base64.b64encode(word).decode()}))))
        # Queued in order before control returns, so live frames can only follow them.
        for i in range(0, len(held), 640):
            asyncio.ensure_future(self._safe(self.ws.send(held[i:i + 640])))
        self.sent_bytes += len(held)
        asyncio.ensure_future(self.listen_watchdog(self.listen_since))

    def duck(self, on: bool, why: str):
        if on != self.ducked:
            log(("ducked: " if on else "full volume: ") + why)
        self.ducked = on
        if on:
            self.duck_since = time.monotonic()
        if self.audio:
            self.audio.volume(DUCK_VOLUME if on else 1.0)

    async def duck_watchdog(self):
        """Never stay ducked once nothing is playing, or once the brain has gone quiet."""
        while True:
            await asyncio.sleep(0.5)
            if not self.ducked:
                continue
            if self.state == "listening":
                self.duck_since = time.monotonic()
            elif not (self.speaking or time.monotonic() < self.playing_until):
                self.duck(False, "nothing playing")
            elif time.monotonic() - self.duck_since > DUCK_LIMIT_S:
                self.duck(False, "no decision from the brain")

    def standdown(self, why: str = ""):
        if self.state != "idle":
            log("stood down" + (f" ({why})" if why else "") + ", nothing sent")
        if self.ducked:
            self.duck(False, "stood down")
        self.state = "idle"
        self.held = bytearray()
        self.wake_pcm = b""
        self.chime_pending = False

    def start_followup(self):
        """The reply asked something: open the mic without the wake word, as the cube does."""
        if not self.ws or self.state != "idle":
            return
        self.state = "listening"
        self.listen_since = time.monotonic()
        self.sent_bytes = 0
        asyncio.ensure_future(self._safe(self.ws.send(json.dumps({"type": "wake", "word": False, "sure": False}))))
        asyncio.ensure_future(self.listen_watchdog(self.listen_since))

    async def listen_watchdog(self, since):
        await asyncio.sleep(75)
        if self.state == "listening" and self.listen_since == since:
            log("no answer from the server while listening, giving up")
            self.state = "idle"

    def forward(self, pcm: bytes):
        if self.ws and self.state == "listening":
            self.sent_bytes += len(pcm)
            asyncio.ensure_future(self._safe(self.ws.send(pcm)))

    def ear_state(self, on: bool):
        """The open ear starts or stops: the brain reads the audio between as heard over its reply."""
        if on:
            self.ear_bytes = 0
        else:
            log(f"open ear closed: {getattr(self, 'ear_bytes', 0)} bytes sent while the reply played")
        if self.ws:
            asyncio.ensure_future(self._safe(self.ws.send(json.dumps({"type": "ear", "on": on}))))

    def forward_ear(self, pcm: bytes):
        if self.ws and self.ear_on:
            self.ear_bytes = getattr(self, "ear_bytes", 0) + len(pcm)
            asyncio.ensure_future(self._safe(self.ws.send(pcm)))

    async def _safe(self, coro):
        try:
            await coro
        except Exception as e:
            log("send failed:", repr(e))

    # ---- playback

    def play_local(self, pcm: bytes):
        self.drained.clear()
        self.audio.play(pcm)
        self.audio.finish()
        self.playing_until = max(time.monotonic(), self.playing_until) + len(pcm) / (OUT_RATE * 2)

    def queue_reply(self, pcm: bytes):
        self.drained.clear()
        self.audio.play(pcm)
        start = max(time.monotonic(), self.playing_until)
        self.playing_until = start + len(pcm) / (OUT_RATE * 2)
        if self.tag:
            # Its own "Sunday" is cancelled by voice processing, but not perfectly: keep a margin.
            self.self_trigger_until = self.playing_until + 0.9

    # ---- server messages

    async def handle(self, msg):
        if isinstance(msg, bytes):
            self.queue_reply(msg)
            return
        m = json.loads(msg)
        t = m.get("type")
        if t == "go":
            await self.go(bool(m.get("hold")))
        elif t == "standdown":
            self.standdown(m.get("why", ""))
        elif t == "speech":
            if self.chime_pending and self.state == "listening" and not self.voice_muted:
                ding()
            self.chime_pending = False
            self.followup_gen += 1     # his words were taken: their answer brings its own window
        elif t == "stop_listening":
            if self.state == "listening":
                log(f"mic closed: {self.sent_bytes} bytes sent")
            self.state = "idle" if self.state == "listening" else self.state
            self.chime_pending = False
        elif t == "sentence":
            self.tag = bool(TRIGGER_WORDS.search(m.get("text", "").lower()))
        elif t == "speak_start":
            self.speaking = True
        elif t == "speak_end":
            self.audio.finish()
            self.speaking = False
        elif t == "stop_audio":
            self.followup_gen += 1
            self.audio.stop()
            self.speaking = False
            self.playing_until = 0.0
            self.duck(False, "stopped")
        elif t == "duck":
            self.duck(bool(m.get("on")), "he is talking to another device" if m.get("on")
                      else "the brain says it carries on")
        elif t == "mute":
            self.voice_muted = bool(m.get("muted"))
            log("voice mute", "on" if self.voice_muted else "off")
        elif t == "computer_audio":
            self.computer_playing = bool(m.get("playing"))
            self.mac_playing = MACHINE in (m.get("machines") or [])
        elif t == "listen":
            asyncio.ensure_future(self.listen_after_reply())
        elif t == "error":
            log("server error:", m.get("text"))
            if self.state == "listening":
                self.state = "idle"

    async def listen_after_reply(self):
        gen = self.followup_gen
        while self.speaking or time.monotonic() < self.playing_until:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.15)
        until = time.monotonic() + FOLLOWUP_WAIT_S
        while self.state != "idle" and gen == self.followup_gen and time.monotonic() < until:
            await asyncio.sleep(0.05)
        if gen != self.followup_gen:
            log("follow-up window dropped: he spoke over the reply or stopped it")
            return
        if self.state != "idle":
            log("follow-up window dropped: a listen over the reply was still open")
            return
        self.start_followup()

    # ---- connection

    async def presence(self):
        while True:
            away = not await asyncio.to_thread(at_home)
            if away != self.away:
                self.away = away
                log("away from home" if away else "at home")
                if self.ws:
                    await self._safe(self.ws.send(json.dumps({"type": "presence", "away": away})))
            await asyncio.sleep(30)

    async def connect_forever(self):
        delay = 1
        while True:
            try:
                async with websockets.connect(SERVER, max_size=None, open_timeout=10, ping_interval=20) as ws:
                    await ws.send(json.dumps({"type": "hello", "device": DEVICE, "role": "ear",
                                              "away": bool(self.away)}))
                    self.ws = ws
                    log("connected to", SERVER)
                    delay = 1
                    async for msg in ws:
                        await self.handle(msg)
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
                if delay == 1 or delay >= 30:
                    log("connection lost:", repr(e)[:120])
            self.ws = None
            if self.state != "idle":
                self.standdown("disconnected")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.audio = Audio(self.on_audio, self.on_helper_event)
        self.away = not await asyncio.to_thread(at_home)
        log("ear started,", "away from home" if self.away else "at home")
        await asyncio.gather(self.connect_forever(), self.presence(), self.duck_watchdog())


if __name__ == "__main__":
    asyncio.run(Ear().run())
