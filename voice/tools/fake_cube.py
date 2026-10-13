#!/usr/bin/env python3
"""Stand in for the cube: speak a sentence with macOS `say`, stream it to the
brain as a cube would, save the reply to reply.wav and report the latency.

    tools/fake_cube.py "What's a good name for a cat?" [ws://<host>:8794]
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import websockets


def _host_lan():
    """The voice host's LAN address from the owner's profile (server/mtoa_config.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mtoa_config", str(Path(__file__).resolve().parent.parent / "server" / "mtoa_config.py"))
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg.get("host.lan_ip") or cfg.get("host.lan_name") or "localhost", cfg.get("host.voice_port", 8794)


text = sys.argv[1] if len(sys.argv) > 1 else "What time is it?"
url = sys.argv[2] if len(sys.argv) > 2 else "ws://%s:%s" % _host_lan()


def speech_pcm(sentence: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "q.wav"
        subprocess.run(["say", "-o", str(wav), "--data-format=LEI16@16000", sentence], check=True)
        with wave.open(str(wav)) as w:
            return w.readframes(w.getnframes())


async def main():
    pcm = speech_pcm(text) + b"\0" * 32000 * 3  # trailing silence so VAD commits
    reply = bytearray()
    rate = 16000
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device": "fake-cube"}))
        await ws.send(json.dumps({"type": "wake"}))
        t_end = None

        async def mic():
            nonlocal t_end
            for i in range(0, len(pcm), 640):  # 20 ms frames, in real time
                await ws.send(pcm[i:i + 640])
                if i >= len(pcm) - 96000 and t_end is None:
                    t_end = time.monotonic()
                await asyncio.sleep(0.02)

        sender = asyncio.create_task(mic())
        t_first = None
        async for msg in ws:
            if isinstance(msg, bytes):
                if t_first is None:
                    t_first = time.monotonic()
                reply.extend(msg)
                continue
            m = json.loads(msg)
            if m["type"] == "speak_start":
                rate = m.get("rate", 16000)
            if m["type"] == "stop_listening":
                sender.cancel()
            if m["type"] in ("speak_end", "error"):
                break
    out = Path(tempfile.gettempdir()) / "sunday-reply.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(bytes(reply))
    if t_first and t_end:
        print(f"first audio {int((t_first - t_end) * 1000)} ms after the speech ended; {len(reply) / (rate * 2):.1f}s of reply")
    subprocess.run(["afplay", str(out)])


asyncio.run(main())
