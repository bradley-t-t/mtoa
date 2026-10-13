#!/usr/bin/env python3
"""Talk over Sunday as a simulated cube or Mac ear, and show what the brain does.

Three cases, each on its own long piece of speech:
  continue  an announcement, then a wake and a new question mid-sentence: the
            announcement must play to the end and the new answer after it
  stop      an announcement, then a wake and "stop": cut at once
  redirect  a question with a long answer, then "no, I meant the other one" mid-answer:
            cut, and answered again

    tools/barge_test.py [continue|stop|redirect|all] [--url ws://voice-host:8795] [--port 8795] [--ear]

--port is the brain's port on sunday, for /say (it only answers on the Pi itself).
--ear connects as the Mac's ear, away from home, so the brain answers through it.
Playback is simulated: each clause plays when it arrives or when the one before it
ends, whichever is later, and a stop_audio drops whatever had not played yet.
"""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
import urllib.parse
import wave
from pathlib import Path

import websockets

ANNOUNCEMENTS = {
    "continue": ("Here is the long version of this evening's plan. The roast goes in at five thirty and needs "
                 "about two hours at three hundred and fifty degrees. While that cooks, the potatoes get peeled "
                 "and boiled, then mashed with butter and a little garlic. The green beans go on at seven, "
                 "steamed for six minutes and finished with lemon. Dessert is the apple pie from the freezer, "
                 "which wants forty minutes in the oven once the roast comes out. Guests arrive at seven thirty."),
    "stop": ("A long note about the car before the trip. The front tires are at twenty eight pounds and want "
             "thirty five, so stop at the station on Main before the highway. The oil change sticker says it was "
             "due two hundred miles ago, and the shop on Fifth can take it Saturday at nine. The wiper blades are "
             "streaking, and a new pair is in the garage on the shelf above the paint cans. Pack the jumper cables "
             "too, because the battery is four years old and the mornings are getting cold."),
}
QUESTIONS = {
    "continue": "What is the capital of France?",
    "stop": "Okay, stop talking.",
    "redirect_ask": "Describe the four largest moons of Jupiter, with one sentence on each.",
    "redirect": "No, I meant the other one, Saturn's moons.",
}


def speech_pcm(sentence: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "q.wav"
        subprocess.run(["say", "-o", str(wav), "--data-format=LEI16@16000", sentence], check=True)
        with wave.open(str(wav)) as w:
            return w.readframes(w.getnframes())


class Device:
    def __init__(self, url: str, ear: bool):
        self.url, self.ear = url, ear
        self.t0 = time.monotonic()
        self.events = []            # (t, kind, detail)
        self.clauses = []           # [text, arrived, bytes, play_start, play_end, cut]
        self.play_end = 0.0
        self.listening = False
        self.go = asyncio.Event()
        self.stopped_listening = asyncio.Event()
        self.speaking_started = asyncio.Event()
        self.sentences = asyncio.Event()

    def now(self):
        return time.monotonic() - self.t0

    def ev(self, kind, detail=""):
        self.events.append((self.now(), kind, detail))

    async def connect(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        hello = {"type": "hello", "device": "fake-mac" if self.ear else "fake-cube"}
        if self.ear:
            hello.update(role="ear", away=True)
        await self.ws.send(json.dumps(hello))
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for msg in self.ws:
            t = self.now()
            if isinstance(msg, bytes):
                if self.clauses:
                    c = self.clauses[-1]
                    secs = len(msg) / 96000
                    if c[3] is None:
                        c[3] = max(t, self.play_end)
                        self.play_end = c[3]
                    self.play_end = max(t, self.play_end) + secs
                    c[2] += len(msg)
                    c[4] = self.play_end
                continue
            m = json.loads(msg)
            k = m.get("type")
            if k == "sentence":
                self.clauses.append([m.get("text", ""), t, 0, None, None, False])
                self.sentences.set()
            elif k == "stop_audio":
                for c in self.clauses:
                    if c[4] and c[4] > t:
                        c[5] = True
                        c[4] = max(t, c[3] or t)
                self.play_end = t
                self.ev("stop_audio")
            elif k == "go":
                self.go.set()
                self.ev("go")
            elif k == "stop_listening":
                self.stopped_listening.set()
                self.ev("stop_listening")
            elif k == "speak_start":
                self.speaking_started.set()
                self.ev("speak_start")
            elif k in ("duck", "standdown", "speak_end", "idle", "listen", "error"):
                self.ev(k, json.dumps({x: y for x, y in m.items() if x != "type"}) if len(m) > 1 else "")

    def playing(self) -> bool:
        return self.play_end > self.now()

    async def talk(self, text: str):
        """Wake it and say this, as the cube streams a wake and its mic audio."""
        voice = speech_pcm(text)
        pcm = voice + b"\0" * 32000 * 3
        self.stopped_listening.clear()
        self.go.clear()
        self.ev("wake", text)
        await self.ws.send(json.dumps({"type": "wake", "word": True, "sure": True, "prob": 0.99, "level": -30}))
        if self.ear:
            await asyncio.wait_for(self.go.wait(), 5)
        for i in range(0, len(pcm), 640):
            if self.stopped_listening.is_set():
                break
            await self.ws.send(pcm[i:i + 640])
            if i <= len(voice) < i + 640:
                self.ev("he stops talking")
            await asyncio.sleep(0.02)
        await asyncio.wait_for(self.stopped_listening.wait(), 10)

    async def quiet_for(self, secs: float, limit: float = 120):
        """Wait until nothing has played or arrived for this long."""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            last = max([self.play_end] + [c[1] for c in self.clauses] + [e[0] for e in self.events]) if self.clauses else 0
            if self.now() - max(last, self.play_end) > secs:
                return
            await asyncio.sleep(0.25)

    def report(self, marks):
        lines = []
        for c in self.clauses:
            text, arrived, size, start, end, cut = c
            if start is None:
                continue
            lines.append((start, f"plays {start:6.2f}-{end:6.2f}s{' CUT' if cut else ''}  {text[:70]}"))
        for t, kind, detail in self.events:
            lines.append((t, f"{kind:>13} {t:6.2f}s  {detail[:70]}"))
        for t, s in sorted(lines, key=lambda x: x[0]):
            print("   ", s)


async def say(port: int, text: str):
    q = urllib.parse.quote(text)
    out = subprocess.run(["ssh", "sunday", f"curl -s 'localhost:{port}/say?text={q}'"], capture_output=True, text=True)
    return out.stdout.strip()


async def case_announce(args, name):
    d = Device(args.url, args.ear)
    await d.connect()
    await asyncio.sleep(1)
    d.ev("/say", await say(args.port, ANNOUNCEMENTS[name]))
    await asyncio.wait_for(d.speaking_started.wait(), 60)
    started = d.now()
    while (d.now() < started + 3 or not d.playing()) and d.now() < started + 30:
        await asyncio.sleep(0.1)
    await d.talk(QUESTIONS[name])
    await d.quiet_for(10)
    print(f"\n== {name} ({'ear' if args.ear else 'cube'})")
    d.report([])
    await d.ws.close()
    return d


async def case_redirect(args):
    d = Device(args.url, args.ear)
    await d.connect()
    await asyncio.sleep(1)
    await d.talk(QUESTIONS["redirect_ask"])
    await asyncio.wait_for(d.speaking_started.wait(), 90)
    # Wait for the answer itself, past any one-line acknowledgement.
    while sum(1 for c in d.clauses if c[3] is not None) < 3 and d.now() < 120:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.5)
    await d.talk(QUESTIONS["redirect"])
    # The correction is answered after Claude has thought about it: wait for that answer.
    asked = d.now()
    while not any(t > asked and k in ("stop_audio", "duck") for t, k, _ in d.events) and d.now() < asked + 30:
        await asyncio.sleep(0.1)
    decided = d.now()
    while not any(c[1] > decided for c in d.clauses) and d.now() < decided + 90:
        await asyncio.sleep(0.2)
    await d.quiet_for(6, 150)
    print(f"\n== redirect ({'ear' if args.ear else 'cube'})")
    d.report([])
    await d.ws.close()
    return d


def _host_lan():
    """The voice host's LAN address from the owner's profile (server/mtoa_config.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mtoa_config", str(Path(__file__).resolve().parent.parent / "server" / "mtoa_config.py"))
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg.get("host.lan_ip") or cfg.get("host.lan_name") or "localhost", cfg.get("host.voice_port", 8794)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?", default="all")
    ap.add_argument("--url", default="ws://%s:8795" % _host_lan()[0])
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--ear", action="store_true")
    args = ap.parse_args()
    for name in (["continue", "stop", "redirect"] if args.case == "all" else [args.case]):
        if name == "redirect":
            await case_redirect(args)
        else:
            await case_announce(args, name)
        await asyncio.sleep(3)


asyncio.run(main())
