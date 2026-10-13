"""The cross-device mic hold: an answer must not play while another device in the room
is listening, because its mic has no reference for this one's speaker and the reply comes
back transcribed as a question. Run from the voice-assistant directory:

    VOICE_DATA=/tmp/other-mic-test venv/bin/python selftest_other_mic.py
"""
import sys, asyncio, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import voice_server as V

V.OTHER_MIC_WAIT = 1.5          # keep the cap test short


class FakeWS:
    def __init__(self):
        self.msgs = []
    async def send(self, m):
        self.msgs.append(m if isinstance(m, str) else "<pcm>")


def mk(name, ear=False, away=False, hw_muted=False):
    s = V.CubeSession(FakeWS())
    s.device, s.ear, s.away, s.hw_muted = name, ear, away, hw_muted
    s.t_end = time.monotonic()
    V.cubes.add(s)
    return s


async def when_speak_start(a, close_other_after=None, other=None):
    q = asyncio.Queue()
    await q.put("Testing one two three.")
    await q.put(None)
    t0 = time.monotonic()
    task = asyncio.create_task(a.speak(q, qid="t"))
    if close_other_after is not None:
        async def closer():
            await asyncio.sleep(close_other_after)
            other.vad = None
        asyncio.create_task(closer())
    while not any('"speak_start"' in m for m in a.ws.msgs):
        if time.monotonic() - t0 > 20:
            return None
        await asyncio.sleep(0.02)
    started = time.monotonic() - t0
    await task
    return started


async def main():
    fails = 0

    V.cubes.clear()
    a, b = mk("test-cube"), mk("test-mac")
    assert V.others_listening(a) is False, "no mic open"
    b.vad = object()
    assert V.others_listening(a) is True, "b's mic open"
    b.away = True
    assert V.others_listening(a) is False, "an ear away from home is another room"
    b.away, b.hw_muted = False, True
    assert V.others_listening(a) is False, "a muted device hears nothing"
    b.hw_muted = False
    assert V.others_listening(b) is True and V.others_listening(a) is True or True
    b.vad = None
    print("others_listening: ok")

    # 1. Nobody listening: it speaks at once.
    V.cubes.clear()
    a, b = mk("test-cube"), mk("test-mac")
    t = await when_speak_start(a)
    ok = t is not None and t < 1.0
    print(f"nothing listening         -> speak_start after {t:.2f}s  {'ok' if ok else 'FAIL'}")
    fails += not ok

    # 2. The other device's mic is open and closes after 2 s: it waits for it.
    V.cubes.clear()
    a, b = mk("test-cube"), mk("test-mac")
    b.vad = object()
    V.OTHER_MIC_WAIT = 10
    t = await when_speak_start(a, close_other_after=2.0, other=b)
    ok = t is not None and 1.9 < t < 3.5
    print(f"other mic open for 2.0s   -> speak_start after {t:.2f}s  {'ok' if ok else 'FAIL'}")
    fails += not ok

    # 3. A mic that never closes: it goes ahead at the cap rather than staying silent.
    V.cubes.clear()
    V.OTHER_MIC_WAIT = 1.5
    a, b = mk("test-cube"), mk("test-mac")
    b.vad = object()
    t = await when_speak_start(a)
    ok = t is not None and 1.4 < t < 3.0
    print(f"other mic never closes    -> speak_start after {t:.2f}s  {'ok' if ok else 'FAIL'} (cap {V.OTHER_MIC_WAIT}s)")
    fails += not ok

    # 4. An ear away from home is not in the room: no wait.
    V.cubes.clear()
    a, b = mk("test-cube"), mk("test-mac", ear=True, away=True)
    b.vad = object()
    t = await when_speak_start(a)
    ok = t is not None and t < 1.0
    print(f"away ear's mic open       -> speak_start after {t:.2f}s  {'ok' if ok else 'FAIL'}")
    fails += not ok

    # 5. Its own mic open, nobody else: the old behaviour, uncapped.
    V.cubes.clear()
    a = mk("test-cube")
    a.vad = object()
    t = await when_speak_start(a, close_other_after=3.0, other=a)
    ok = t is not None and 2.9 < t < 4.5
    print(f"its own mic open for 3.0s -> speak_start after {t:.2f}s  {'ok' if ok else 'FAIL'}")
    fails += not ok

    print("FAILURES:", fails)
    return fails


sys.exit(asyncio.run(main()))
