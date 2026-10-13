#!/usr/bin/env python3
"""How soon the cube's wake word hears "Sunday" after it stops talking, replayed offline.

fixtures/line-end*.raw are eight to ten seconds of the cube's raw mics and loopback (the
"record" test hook) around one line it said, 2026-10-07: "Microphone check thirteen." (-2)
and "Testing the speaker by the front door." (-4) end in words its wake word scores as its
name. Each is played back through the cube's own echo canceller and wake model, with his real
"Sunday" (fixtures/his-sunday.raw) mixed into the mic at a range of offsets from the moment
its speaker actually went quiet, two ways:

- before: one wake model, its feed switched from the raw cancelled audio to the denoised
  audio when the speech queue ran out. Its features stayed adapted to its own voice, and it
  was deaf to "Sunday" for most of a second after it stopped (what Alex heard as no reply).
- now: a model per feed, each fed its own feed all the time (sunday_cube.py), the raw one
  deciding while speech is queued and the quiet-room one after, re-armed once it no longer
  scores its own voice; beside it a fresh model started each time its speaker goes quiet
  with nothing left queued, deciding once the room is quiet (the echo tail after it). A
  model that heard its own last words scored as its name stays deaf to "Sunday" for most of
  a second after; a fresh one does not.

It must never wake on its own voice, and now must wake on "Sunday" said from 0.2 s after its
speaker stopped (0.0 to 0.1 s is reported, not failed: a fresh model has not yet heard enough
of the room to be sure there). Runs on the cube, which has the echo canceller and the models:

    cat wake_replay.py | ssh cube 'mkdir -p /tmp/wake-replay && cat > /tmp/wake-replay/wake_replay.py'
    (copy fixtures/*.raw to /tmp/wake-replay/fixtures/ the same way)
    ssh cube 'cd /tmp/wake-replay && python3 wake_replay.py'      # every fixtures/line-end*.raw
"""

import contextlib
import io
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/data/sunday")
with contextlib.redirect_stdout(io.StringIO()):
    import sunday_cube as sc
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

F = 160                     # 10 ms
ALSA = 24                   # frames still in the speaker's buffer when the queue runs out
HERE = Path(__file__).resolve().parent / "fixtures"
cfg = sc.load_config()
gain = 10 ** (cfg["mic_gain_db"] / 20)
word = np.fromfile(HERE / "his-sunday.raw", "<i2").astype(np.float32)
prime = np.fromfile(HERE / "his-speech.raw", "<i2").astype(np.float32)
TAIL = int(sc.ROOM_TAIL_S * 100)    # 10 ms frames of echo tail after the loopback goes quiet
OFFSETS = (0.0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0)
MUST = 0.2                           # "Sunday" this long after it went quiet, or later, must wake it
rec = None


def model():
    with contextlib.redirect_stdout(io.StringIO()):
        return MicroWakeWord.from_config(sc.HOME / "wake" / f"{cfg['wake_word']}.json", sc.TFLITE)


def mean(m) -> float:
    p = m._probabilities
    return sum(p) / len(p) if p else 0.0


def replay(at):
    """(frame its speaker went quiet, [(frame, raw model, quiet model, the one model before,
    fresh model, its speaker still heard)])."""
    n = len(rec) // F
    ref = rec[:n * F, 2].astype(np.float32).reshape(n, F)
    on = (ref ** 2).mean(1) > 1e4
    end = int(np.where(on)[0][-1]) + 1
    aec = sc.EchoCanceller(cfg["echo_tail_ms"])
    for _ in range(3):        # the gain his voice leaves it at, as it is after any use
        for i in range(0, len(prime) // F * F, F):
            aec.process(np.clip(prime[i:i + F] / gain, -32768, 32767).astype("<i2").tobytes(), b"\0" * F * 2)
    fl, fq, fs = MicroWakeWordFeatures(), MicroWakeWordFeatures(), MicroWakeWordFeatures()
    ml, mq, ms = model(), model(), model()
    ff = mf = None
    start = None if at is None else end + int(at * 100)
    rows = []
    last_on = -10 ** 6
    for k in range(n):
        if on[k]:
            last_on = k
        elif k and on[k - 1] and k >= end - ALSA:
            ff, mf = MicroWakeWordFeatures(), model()    # quiet, with nothing left queued
        sounding = k - last_on < TAIL
        mic = rec[k * F:(k + 1) * F, 0].astype(np.float32)
        if start is not None and start <= k < start + len(word) // F:
            mic = mic + word[(k - start) * F:(k - start + 1) * F] / gain
        cancelled, processed = aec.process(np.clip(mic, -32768, 32767).astype("<i2").tobytes(),
                                           rec[k * F:(k + 1) * F, 2].copy().tobytes())
        loud = np.clip(np.frombuffer(cancelled, "<i2").astype(np.float32) * gain, -32768, 32767).astype("<i2").tobytes()
        a, b = list(fl.process_streaming(loud)), list(fq.process_streaming(processed))
        c = list(fs.process_streaming(loud if k < end - ALSA else processed))
        d = list(ff.process_streaming(processed)) if ff is not None else []
        for i in range(len(a)):
            ml.process_streaming(a[i])
            if i < len(b):
                mq.process_streaming(b[i])
            if i < len(c):
                ms.process_streaming(c[i])
            if i < len(d):
                mf.process_streaming(d[i])
            rows.append((k, mean(ml), mean(mq), mean(ms), mean(mf) if mf is not None else 0.0, sounding))
    return end, rows


def fires(end, rows, way) -> list:
    switch = end - ALSA
    quiet_cut = cfg["wake_cutoff"]
    armed, out = True, []
    for k, raw, quiet, one, fresh, sounding in rows:
        talking = k < switch
        if way == "before":
            hit = one >= (cfg["speaking_wake_cutoff"] if talking else quiet_cut)
        elif talking:
            armed = False
            hit = raw >= cfg["speaking_wake_cutoff"]
        else:
            if not armed and quiet < sc.REARM:
                armed = True
            hit = (quiet >= quiet_cut and armed) or (fresh >= quiet_cut and not sounding)
        if hit:
            out.append(k)
    return out


def one_recording(path) -> int:
    global rec
    rec = np.fromfile(path, "<i2").reshape(-1, 4)
    bad = 0
    end, rows = replay(None)
    print(f"{Path(path).name}: its speaker went quiet {end / 100:.2f} s into the recording")
    for way in ("before", "now"):
        f = fires(end, rows, way)
        # While speech is still queued a wake on its own voice is the raw model's, at its higher
        # cutoff, and the brain drops it (no name in what it heard); after that, never.
        after = [x for x in f if x >= end - ALSA]
        bad += bool(after) and way == "now"
        print(f"{'FAIL' if after and way == 'now' else 'ok  '} {way}, nothing said: "
              + (f"woke on its own voice {', '.join(f'{(x - end) / 100:+.2f} s' for x in f[:4])}"
                 + ("" if after else " (while speech was queued)") if f else "never woke"))
    for at in OFFSETS:
        end, rows = replay(at)
        start = end + int(at * 100)
        stop = start + len(word) // F + 60
        line, missed = [], False
        for way in ("before", "now"):
            switch = end - ALSA
            seen = [(one if way == "before" else raw if k < switch else max(quiet, fresh))
                    for k, raw, quiet, one, fresh, _ in rows if start <= k < stop]
            woke = any(start <= k < stop for k in fires(end, rows, way))
            missed = missed or (way == "now" and not woke)
            line.append(f"{way} {max(seen):.2f}{' woke' if woke else ' missed'}")
        bad += missed and at >= MUST
        print(f"{'ok  ' if not missed else 'FAIL' if at >= MUST else 'late'} \"Sunday\" {at:.2f} s after it went quiet: "
              + ", ".join(line))
    return bad


def main() -> int:
    paths = sys.argv[1:] or sorted(HERE.glob("line-end*.raw"))
    bad = sum(one_recording(p) for p in paths)
    print("all passed" if not bad else f"{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
