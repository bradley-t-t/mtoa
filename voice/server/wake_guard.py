#!/usr/bin/env python3
"""A wake with no words of his in it shows nothing anywhere: checked against the running brain.

It connects as a cube of its own ("wake-guard") and as a popup, and wakes the brain with
his real coughs from fixtures/nonspeech (the wake word fired on each of them on 2026-10-06),
with a television after the wake (fixtures/nonspeech/word-tv-*), and with a "Sunday" and
nothing after it. None of them may chime or light the cube ("speech") or turn anything down
("duck"), the popups must get nothing that shows ("listening", "partial", "status", "heard",
"reply", "speaking"), and the listen must end without a reply. Then "Sunday" and "What time
is it?" must get its "speech" only after the question began, and something on the popups,
and how long after the wake that came is the chime's delay. Then a pretend Mac ear wakes it while the
pretend cube plays and just after it goes quiet: it must stand down only while the cube could
have been heard. Nothing here makes a sound in the room: whatever the brain sends goes to
these pretend devices.

    venv/bin/python wake_guard.py [ws://127.0.0.1:8794]     # on the Pi, with the service up
"""

import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets

RATE = 16000
FRAME = 320                 # 20 ms, as a cube sends it
PREROLL = RATE * 3 // 2     # the 1.5 s before the wake fired, sent with the wake
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nonspeech"
# The brain's own record of each wake it dropped, with its id: what tells this wake's events
# from those of whatever the owner is doing at the same time. Without it, every event counts.
SPEECH_LOG = Path(__file__).resolve().parent / "data" / "speech.jsonl"
SHOWS = {"listening", "partial", "status", "heard", "reply", "speaking"}
LOUD = {"duck", "speech"}


def pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


async def one(url: str, name: str, audio: bytes, events: list) -> dict:
    """Wake the brain on this cube with audio's first 1.5 s as the wake word and the rest as
    what followed; stream it in real time, then silence, until the brain closes the mic."""
    got = []
    log_at = SPEECH_LOG.stat().st_size if SPEECH_LOG.exists() else None
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device": "wake-guard", "hw_muted": False}))
        await ws.send(json.dumps({"type": "light", "on": False}))
        await asyncio.sleep(0.3)
        pre, rest = audio[:PREROLL * 2], audio[PREROLL * 2:]
        pre = b"\0" * (PREROLL * 2 - len(pre)) + pre
        rest += b"\0" * RATE * 2 * 8
        mark = len(events)
        t0 = time.monotonic()
        await ws.send(json.dumps({"type": "wake", "word": True, "sure": True, "prob": 0.96, "level": -30.0,
                                  "by": "word", "echo": {"playing": False}, "preroll": base64.b64encode(pre).decode()}))

        closed = asyncio.Event()

        async def listen():
            async for m in ws:
                if isinstance(m, str):
                    d = json.loads(m)
                    got.append((time.monotonic() - t0, d.get("type")))
                    if d.get("type") in ("stop_listening", "idle"):
                        closed.set()

        reader = asyncio.ensure_future(listen())
        for i in range(0, len(rest), FRAME * 2):
            if closed.is_set():
                break
            await ws.send(rest[i:i + FRAME * 2])
            await asyncio.sleep(FRAME / RATE)
        try:
            await asyncio.wait_for(closed.wait(), 10)
        except asyncio.TimeoutError:
            pass
        # The turn deciding once the mic closed, the directed judge included: a chime held until
        # its words were judged comes in this time, or never.
        await asyncio.sleep(6.0)
        reader.cancel()
    mine = None
    if log_at is not None:
        with open(SPEECH_LOG, "rb") as f:
            f.seek(log_at)
            mine = {d.get("qid") for d in map(json.loads, f.read().decode().splitlines()) if d.get("device") == "wake-guard"}
    shown = [(round(t - t0, 2), e["type"]) for t, e in events[mark:] if e.get("type") in SHOWS
             and (mine is None or e.get("id") in mine)]
    others = [(round(t - t0, 2), e["type"]) for t, e in events[mark:] if e.get("type") in SHOWS
              and mine is not None and e.get("id") not in mine]
    lights = [round(t - t0, 2) for t, e in events[mark:] if e.get("type") == "light" and e.get("on")]
    return {"name": name, "sent": got, "shown": shown, "lights": lights, "others": others}


async def ear_after_quiet(url: str, quiet_for: float | None) -> str:
    """The pretend cube is heard playing, then goes quiet (as a cube reports it); a Mac wake
    arrives quiet_for seconds later (None: while it still plays). What the Mac is told."""
    async with websockets.connect(url) as cube, websockets.connect(url) as ear:
        await cube.send(json.dumps({"type": "hello", "device": "wake-guard", "hw_muted": False}))
        await ear.send(json.dumps({"type": "hello", "device": "guard-ear", "role": "ear"}))
        await asyncio.sleep(0.3)
        await cube.send(json.dumps({"type": "playing", "on": True, "tail_ms": 90}))
        await asyncio.sleep(0.5)
        if quiet_for is not None:
            await cube.send(json.dumps({"type": "playing", "on": False, "tail_ms": 90}))
            await asyncio.sleep(quiet_for)
        await ear.send(json.dumps({"type": "wake", "word": True, "sure": True, "prob": 0.9, "level": -25.0}))
        try:
            while True:
                d = json.loads(await asyncio.wait_for(ear.recv(), 3))
                if d.get("type") in ("go", "standdown"):
                    return d["type"] + (": " + d["why"] if d.get("why") else "")
        except asyncio.TimeoutError:
            return "nothing"
        finally:
            await cube.send(json.dumps({"type": "playing", "on": False, "tail_ms": 90}))


async def main(url: str) -> int:
    events = []

    async def watch():
        async with websockets.connect(url.rstrip("/") + "/events", max_size=None) as v:
            async for m in v:
                events.append((time.monotonic(), json.loads(m)))

    viewer = asyncio.ensure_future(watch())
    await asyncio.sleep(0.5)
    bad = 0
    for p in sorted(FIXTURES.glob("cough-*.wav")) + sorted(FIXTURES.glob("word-tv-*.wav")):
        r = await one(url, p.name, pcm(p), events)
        loud = [(round(t, 2), k) for t, k in r["sent"] if k in LOUD]
        ok = not loud and not r["shown"]
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {p.name}: sent the cube {[k for _, k in r['sent']]}"
              f"{', shown ' + str(r['shown']) if r['shown'] else ', nothing shown'}"
              f"{' (meanwhile, for something else: ' + str(r['others']) + ')' if r['others'] else ''}"
              f"{' (a cube ring lit at ' + str(r['lights']) + ' s: the real cube, never this one)' if r['lights'] else ''}")
    # Its name alone asks for nothing: no chime, nothing shown. It leaves no record to tell its
    # events by, so only what the pretend cube was sent counts.
    name = pcm(FIXTURES / "wake-tts-sunday.wav")
    name = b"\0" * (PREROLL * 2 - len(name)) + name
    r = await one(url, "wake-tts-sunday.wav", name, events)
    loud = [(round(t, 2), k) for t, k in r["sent"] if k in LOUD]
    bad += bool(loud)
    print(f"{'FAIL' if loud else 'ok  '} wake-tts-sunday.wav, its name and nothing after: sent the cube "
          f"{[k for _, k in r['sent']]}")
    # Its name, then a question: the chime and the overlay come once the question is heard and
    # judged, which for a question this short is often only once he has stopped.
    ask = pcm(FIXTURES / "word-tts-what-time.wav")
    lead = next((i for i in range(0, len(ask), 2) if abs(int.from_bytes(ask[i:i + 2], "little", signed=True)) > 1000),
                0) / 2 / RATE
    r = await one(url, "word-tts-what-time.wav after its name", name + ask, events)
    speech = [t for t, k in r["sent"] if k == "speech"]
    closed = next((t for t, k in r["sent"] if k == "stop_listening"), None)
    ok = bool(speech) and bool(r["shown"]) and speech[0] > lead
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} \"Sunday\", then \"What time is it?\" (begun {lead:.2f} s after the wake): "
          + (f"chimed {speech[0]:.2f} s after the wake ({'while the mic was open' if closed is None or speech[0] < closed else f'{speech[0] - closed:.2f} s after it closed'}), "
             f"shown {r['shown']}" if speech
             else f"never cleared: sent the cube {[k for _, k in r['sent']]}, shown {r['shown']}"))
    viewer.cancel()
    # A Mac wake stands down while a cube can be heard, and goes ahead the moment a word he
    # began after the cube went quiet can arrive (0.62 s at the soonest).
    for quiet_for, want in ((None, "standdown"), (0.1, "standdown"), (0.65, "go")):
        got = await ear_after_quiet(url, quiet_for)
        ok = got.startswith(want)
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} Mac wake "
              f"{'while the cube plays' if quiet_for is None else f'{quiet_for:.2f} s after the cube went quiet'}: {got}")
        await asyncio.sleep(2.0)
    print("all passed" if not bad else f"{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8794")))
