#!/usr/bin/env python3
"""Any computer's microphone and speaker as one of Sunday's ears.

The cube and the Mac app are the ears built for the house. This is the one for any
other machine with a microphone and something to play sound through: Linux, Windows,
a Mac without the app, a Raspberry Pi with a USB speakerphone. It is the Mac ear
(popup/ear/mac_ear.py) with its audio done by PortAudio through sounddevice instead of
the Mac's helper, so it hears the same wake word, defers to a cube the same way and
speaks the same protocol to the voice server.

There is no echo cancellation here, so by default it stops listening while it talks:
what it plays is never heard back as a question, and a reply cannot be talked over. A
speakerphone that cancels its own echo can listen throughout with SUNDAY_EAR_DUPLEX=1.

    pip install numpy sounddevice websockets pymicro-wakeword
    python3 clients/mic_ear.py                       # the host in the profile
    SUNDAY_EAR_SERVER=ws://voice-host:8794 SUNDAY_EAR_DEVICE=kitchen python3 clients/mic_ear.py

  SUNDAY_EAR_SERVER     the voice server; defaults to host.lan_ip (or host.lan_name) and
                        host.voice_port from the owner's profile
  SUNDAY_EAR_DEVICE     the name this ear goes by on the server (default "mic")
  SUNDAY_EAR_INPUT      the microphone, by number or part of its name
  SUNDAY_EAR_OUTPUT     the speaker, the same way (python3 -m sounddevice lists both)
  SUNDAY_EAR_PLAIN_GAIN_DB  gain on the microphone (default 0 here; the Mac ear uses 12)

Everything else the Mac ear reads from its environment applies here too.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EAR_DIR = HERE.parent / "popup" / "ear"


def _profile_host() -> dict:
    """The `host` block of the owner's profile, read the way tools/mtoa_config.py reads it."""
    home = Path.home()
    explicit = os.environ.get("MTOA_CONFIG")
    for p in ([Path(explicit)] if explicit else
              [home / ".claude" / "profile" / "mtoa.json", home / ".config" / "mtoa" / "mtoa.json"]):
        try:
            return json.loads(p.read_text()).get("host") or {}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def _default_server() -> str:
    host = _profile_host()
    name = host.get("lan_ip") or host.get("lan_name") or "localhost"
    return "ws://%s:%s" % (name, host.get("voice_port") or 8794)


os.environ.setdefault("SUNDAY_EAR_SERVER", _default_server())
os.environ.setdefault("SUNDAY_EAR_DEVICE", "mic")
os.environ.setdefault("SUNDAY_EAR_PLAIN_GAIN_DB", "0")
# mac_ear reads its helper and wake model from argv; the helper is not used here.
sys.argv = [sys.argv[0], "", str(EAR_DIR / "wake")]
sys.path.insert(0, str(EAR_DIR))

import numpy as np          # noqa: E402
import sounddevice as sd    # noqa: E402

import mac_ear as ear       # noqa: E402

DUPLEX = os.environ.get("SUNDAY_EAR_DUPLEX") == "1"
ECHO_TAIL_S = 0.3           # the room's echo of the last thing played, still not listened to
MIC_FRAME = ear.FRAME_BYTES // 2        # 10 ms of 16 kHz samples
OUT_FRAME = ear.OUT_RATE // 100         # 10 ms of 48 kHz samples


def _device(var: str):
    value = os.environ.get(var)
    if not value:
        return None
    return int(value) if value.isdigit() else value


class DeviceAudio:
    """The Mac helper's interface over PortAudio: microphone frames in, reply audio out.

    The microphone goes to the ear twice, as the Mac's does: as the plain microphone, and
    as the stream it listens to while sound plays here. Without echo cancellation that
    second stream is silence while anything plays, which is what makes it half duplex.
    """

    def __init__(self, on_audio, on_event):
        self.on_audio, self.on_event = on_audio, on_event
        self.lock = threading.Lock()
        self.reply = bytearray()        # 48 kHz s16le still to play
        self.chime = bytearray()        # the ding, mixed in without counting as talking
        self.finishing = False
        self.quiet_from = 0.0           # when what was played stops being audible
        self.gain = 1.0
        self.mic = sd.RawInputStream(samplerate=ear.RATE, channels=1, dtype="int16",
                                     blocksize=MIC_FRAME, device=_device("SUNDAY_EAR_INPUT"),
                                     callback=self._heard)
        self.speaker = sd.RawOutputStream(samplerate=ear.OUT_RATE, channels=1, dtype="int16",
                                          blocksize=OUT_FRAME, device=_device("SUNDAY_EAR_OUTPUT"),
                                          callback=self._play)
        self.mic.start()
        self.speaker.start()
        on_event({"event": "started", "input": str(self.mic.device), "output": str(self.speaker.device)})

    def talking(self) -> bool:
        with self.lock:
            return bool(self.reply) or time.monotonic() < self.quiet_from

    def _heard(self, data, frames, when, status):
        pcm = bytes(data)
        self.on_audio(pcm, True)
        self.on_audio(bytes(len(pcm)) if not DUPLEX and self.talking() else pcm, False)

    def _play(self, out, frames, when, status):
        n = frames * 2
        drained = False
        with self.lock:
            chunk = bytes(self.reply[:n])
            del self.reply[:n]
            ding = bytes(self.chime[:n])
            del self.chime[:n]
            if chunk:
                self.quiet_from = time.monotonic() + ECHO_TAIL_S
            if self.finishing and not self.reply:
                self.finishing, drained = False, True
        x = np.zeros(frames, np.float32)
        if chunk:
            x[:len(chunk) // 2] += np.frombuffer(chunk, "<i2").astype(np.float32) * self.gain
        if ding:
            x[:len(ding) // 2] += np.frombuffer(ding, "<i2").astype(np.float32)
        out[:] = np.clip(x, -32768, 32767).astype("<i2").tobytes()
        if drained:
            self.on_event({"event": "drained"})

    def play(self, pcm: bytes):
        with self.lock:
            self.reply += pcm

    def ding(self):
        with self.lock:
            self.chime += ear.chime_pcm(True)

    def finish(self):
        with self.lock:
            if self.reply:
                self.finishing = True
                return
        self.on_event({"event": "drained"})

    def stop(self):
        with self.lock:
            self.reply.clear()
            self.finishing = False
        self.on_event({"event": "drained"})

    def volume(self, v: float):
        self.gain = float(v)


_audio = []


def _make_audio(on_audio, on_event):
    audio = DeviceAudio(on_audio, on_event)
    _audio.append(audio)
    return audio


def _ding():
    ear.dinged_at = time.monotonic()
    if _audio:
        _audio[0].ding()


ear.Audio = _make_audio
ear.ding = _ding
if sys.platform != "darwin":
    # The home-network test reads macOS's own tools. Elsewhere this ear counts as home,
    # so a cube in the same house keeps its priority.
    ear.at_home = lambda: True


if __name__ == "__main__":
    ear.log("mic ear for", ear.SERVER, "as", ear.DEVICE, "(duplex)" if DUPLEX else "(half duplex)")
    ear.asyncio.run(ear.Ear().run())
