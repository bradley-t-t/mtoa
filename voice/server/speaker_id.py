#!/usr/bin/env python3
"""Whose voice it is: the owner's, or anything else that can say "Sunday" near a mic.

The wake word fires on "Sunday" in any audio and the gates after it judge only words, so a
TV, a phone on speaker, the other computer or Sunday's own voice coming back can all be
taken for him. This scores each thing heard against a voiceprint of his, made from his own
voice with NeMo TitaNet-small (sherpa-onnx, 192 numbers per stretch of speech, about 50 ms
for 1.5 s of audio on the Pi).

  score      cosine of what was heard against the centre of his enrolled samples
  reject     below `reject`: not him. Chosen so about 98% or more of his own held-out samples
             pass (each enrolled sample scored against the centre of all the others)
  accept     at or above `accept`: him, plainly. Between the two is borderline, which the
             caller settles with what was said (the directed-or-overheard judge)

Modes, in data/voiceprint/state.json:
  shadow     every wake is scored and logged, nothing is blocked. The start, until the
             samples can set a threshold.
  enforcing  below `reject` is ignored, silently. Switched to by itself once there are
             ENFORCE_MIN samples of him, ENFORCE_WAKES of them from real wakes, and his
             held-out scores clear every voice in the test set that is not his; and back to
             shadow if they stop doing so.

His samples come from his own answered turns that he went on to follow up (passive), and
from a spoken enrollment (Sunday asks once, he answers aloud). The set is bounded and an
outlier against the rest is dropped. Every wake's audio is kept, bounded, in wakes/, so the
threshold can be set again from what really happened.

    speaker_id.py status
    speaker_id.py enroll <wav>...            add his samples from files (each cut into ~3 s stretches)
    speaker_id.py impostors <dir>...         embed voices that are not his (the test set)
    speaker_id.py score <wav>...             score files against the voiceprint
    speaker_id.py retune                     set the thresholds again from the samples
    speaker_id.py mode shadow|enforcing      set the mode by hand (a hand setting is not overruled)
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VOICE_DATA", HERE / "data"))
DIR = DATA / "voiceprint"
MODEL = Path(os.environ.get("VOICE_SPEAKER_MODEL", HERE / "models" / "speaker" / "nemo_en_titanet_small.onnx"))
RATE = 16000

MAX_SAMPLES = int(os.environ.get("VOICE_SPEAKER_MAX", "80"))        # his samples kept
ENFORCE_MIN = int(os.environ.get("VOICE_SPEAKER_ENFORCE_MIN", "20"))  # samples before it can enforce
ENFORCE_WAKES = int(os.environ.get("VOICE_SPEAKER_ENFORCE_WAKES", "10"))  # of them from real wakes
PASS_RATE = 0.98             # his held-out samples that must clear `reject`
MARGIN = 0.03                # `reject` sits this far under his 2nd percentile
MIN_SPEECH = 0.7             # seconds of speech under which a score is not trusted
SCORE_MAX_S = 6.0            # only this much speech is scored: the cost grows with it
WAKES_KEPT = int(os.environ.get("VOICE_SPEAKER_WAKES_KEPT", "400"))
CANDIDATES_MAX = 60          # turns kept as possibly his before there is a voiceprint
BOOTSTRAP_MIN = 8            # a voiceprint is started from this many mutually alike candidates
ANCHOR_ALIKE = 0.25          # ...and, with a thin voiceprint already there, this much like it on average
BOOTSTRAP_ALIKE = 0.5       # how alike two candidates must be to count as one voice
# Until there are enough samples to set a line, a new one must still be this like the ones
# there are: every test voice and every real wake that was not him scored under it.
PROVISIONAL = float(os.environ.get("VOICE_SPEAKER_PROVISIONAL", "0.35"))


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def speech_only(audio: np.ndarray, frame: int = 480) -> np.ndarray:
    """The louder frames of a stretch: silence before and after him, and between his words,
    only waters down whose voice it is."""
    x = np.asarray(audio, dtype=np.float32)
    if len(x) < frame * 4:
        return x
    n = len(x) // frame
    f = x[: n * frame].reshape(n, frame)
    db = 10 * np.log10((f ** 2).mean(axis=1) + 1e-12)
    floor = float(np.percentile(db, 20))
    bar = max(db.max() - 30, -65)
    if db.max() - floor > 15:
        bar = max(bar, floor + 6)        # well above the room's own noise
    keep = db >= min(bar, db.max() - 6)
    # A frame either side of each kept one, so word edges are not clipped.
    keep = keep | np.roll(keep, 1) | np.roll(keep, -1)
    return f[keep].reshape(-1)


class SpeakerCheck:
    def __init__(self, directory: Path = DIR, model: Path = MODEL):
        self.dir = Path(directory)
        self.model = Path(model)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "wakes").mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self._ext = None
        self.samples: list = []        # {"id","at","source","device","seconds","text","emb"}
        self.candidates: list = []     # the same, before there is a voiceprint
        self.state = {"mode": "shadow", "reject": None, "accept": None, "by": "default", "since": time.time(),
                      "why": "no voiceprint yet"}
        self.impostors = np.zeros((0, 192), dtype=np.float32)
        self.impostor_names: list = []
        self.centroid = None
        self.load()

    # ---- storage

    def load(self):
        for name, attr in (("samples.json", "samples"), ("candidates.json", "candidates")):
            p = self.dir / name
            if p.exists():
                try:
                    rows = json.loads(p.read_text())
                    for r in rows:
                        r["emb"] = np.asarray(r["emb"], dtype=np.float32)
                    setattr(self, attr, rows)
                except (ValueError, KeyError, OSError):
                    pass
        p = self.dir / "state.json"
        if p.exists():
            try:
                self.state.update(json.loads(p.read_text()))
            except (ValueError, OSError):
                pass
        p = self.dir / "impostors.npz"
        if p.exists():
            z = np.load(p, allow_pickle=False)
            self.impostors, self.impostor_names = z["emb"].astype(np.float32), list(z["names"])
        self._recentre()

    def _save(self, name: str, rows: list):
        out = [{**r, "emb": [round(float(x), 6) for x in r["emb"]]} for r in rows]
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(out))
        tmp.replace(self.dir / name)

    def save(self):
        self._save("samples.json", self.samples)
        self._save("candidates.json", self.candidates)
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.dir / "state.json")

    def _recentre(self):
        self.centroid = _norm(np.mean([s["emb"] for s in self.samples], axis=0)) if self.samples else None

    # ---- embedding and scoring

    @property
    def ext(self):
        if self._ext is None:
            import sherpa_onnx
            self._ext = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model), num_threads=2, provider="cpu"))
        return self._ext

    def embed(self, audio: np.ndarray) -> tuple:
        """(normalized embedding or None, seconds of speech it was made from)."""
        x = speech_only(audio)[: int(SCORE_MAX_S * RATE)]
        secs = len(x) / RATE
        if secs < 0.3:
            return None, secs
        peak = float(np.abs(x).max()) or 1.0
        x = (x * min(0.8 / peak, 30.0)).astype(np.float32)
        with self.lock:
            s = self.ext.create_stream()
            s.accept_waveform(RATE, x)
            s.input_finished()
            e = np.asarray(self.ext.compute(s), dtype=np.float32)
        return _norm(e), secs

    def score(self, emb) -> float | None:
        if emb is None or self.centroid is None:
            return None
        return round(float(emb @ self.centroid), 3)

    def check(self, audio: np.ndarray) -> dict:
        """Everything about one stretch: {"score", "speech_s", "verdict", "ms", "emb"}.
        verdict: accept | borderline | reject | short (too little speech) | unknown (no voiceprint)."""
        t0 = time.monotonic()
        emb, secs = self.embed(audio)
        sc = self.score(emb)
        return {"score": sc, "speech_s": round(secs, 2), "verdict": self.verdict(sc, secs), "emb": emb,
                "ms": int((time.monotonic() - t0) * 1000)}

    def verdict(self, sc, secs: float) -> str:
        if sc is None:
            return "unknown" if self.centroid is None else "short"
        rej, acc = self.state.get("reject"), self.state.get("accept")
        if rej is None or acc is None:
            return "unknown"
        if sc >= acc:
            return "accept"
        if secs < MIN_SPEECH:
            return "short"      # too little to go on: never a reject by itself
        return "reject" if sc < rej else "borderline"

    @property
    def enforcing(self) -> bool:
        return self.state.get("mode") == "enforcing" and self.state.get("reject") is not None

    # ---- his samples

    def add_sample(self, emb, meta: dict, source: str) -> str:
        """Keep a sample of his voice. Returns what happened, for the log."""
        if emb is None:
            return "no embedding"
        row = {"id": f"{source}-{int(time.time() * 1000)}", "at": time.time(), "source": source,
               "device": meta.get("device", ""), "seconds": meta.get("seconds", 0), "text": meta.get("text", "")[:200],
               "score": meta.get("score"), "emb": np.asarray(emb, dtype=np.float32)}
        if self.centroid is None:
            if source == "enroll":
                self.samples.append(row)
                self._recentre()
                self.retune()
                self.save()
                return "enrolled"
            self.candidates = (self.candidates + [row])[-CANDIDATES_MAX:]
            started = self._bootstrap()
            self.save()
            return "started a voiceprint from candidates" if started else f"candidate {len(self.candidates)}"
        sc = float(row["emb"] @ self.centroid)
        floor = self.state.get("reject")
        provisional = floor is None
        floor = PROVISIONAL if provisional else floor
        if source != "enroll" and sc < floor:
            if not provisional:
                return f"not kept: {sc:.2f} is under the reject line {floor:.2f}"
            # Too few samples yet to be sure what he sounds like: held as a candidate, in case
            # the few samples there are were a poor likeness and his real voice keeps coming back.
            self.candidates = (self.candidates + [row])[-CANDIDATES_MAX:]
            grown = self._bootstrap(anchor=self.centroid)
            self.save()
            return f"candidate ({sc:.2f} under {floor:.2f})" + ("; a group of them joined his samples" if grown else "")
        self.samples.append(row)
        self._prune()
        self._recentre()
        self.retune()
        self.save()
        return f"kept ({sc:.2f}), {len(self.samples)} samples"

    def _bootstrap(self, anchor=None) -> bool:
        """No voiceprint yet (or one too thin to trust): the one voice that keeps coming back in
        turns he followed up is his. Start one from the largest group of candidates that are
        all alike, and with anchor, that are at least somewhat like the samples there are."""
        if len(self.candidates) < BOOTSTRAP_MIN:
            return False
        e = np.stack([c["emb"] for c in self.candidates])
        sims = e @ e.T
        best = max(range(len(e)), key=lambda i: int((sims[i] >= BOOTSTRAP_ALIKE).sum()))
        group = [i for i in range(len(e)) if sims[best, i] >= BOOTSTRAP_ALIKE]
        group = [i for i in group if np.mean([sims[i, j] for j in group if j != i]) >= BOOTSTRAP_ALIKE - 0.05]
        if len(group) < BOOTSTRAP_MIN:
            return False
        if anchor is not None and float(np.mean(e[group] @ anchor)) < ANCHOR_ALIKE:
            return False
        for i in group:
            self.samples.append({**self.candidates[i], "source": "passive"})
        self.candidates = [c for i, c in enumerate(self.candidates) if i not in group]
        self._recentre()
        self.retune()
        return True

    def _prune(self):
        """Drop an outlier against the rest, then the oldest passive sample while over the bound."""
        if len(self.samples) >= 8:
            e = np.stack([s["emb"] for s in self.samples])
            total = e.sum(axis=0)
            loo = np.array([float(_norm(total - e[i]) @ e[i]) for i in range(len(e))])
            med = np.median(loo)
            mad = np.median(np.abs(loo - med)) + 1e-6
            worst = int(np.argmin(loo))
            if loo[worst] < med - 4 * 1.4826 * mad and loo[worst] < med - 0.12:
                self.samples.pop(worst)
        while len(self.samples) > MAX_SAMPLES:
            passive = [i for i, s in enumerate(self.samples) if s["source"] != "enroll"]
            self.samples.pop(passive[0] if passive else 0)

    # ---- thresholds

    def held_out(self) -> np.ndarray:
        """Each of his samples scored against the centre of all the others."""
        if len(self.samples) < 3:
            return np.zeros(0)
        e = np.stack([s["emb"] for s in self.samples])
        total = e.sum(axis=0)
        return np.array([float(_norm(total - e[i]) @ e[i]) for i in range(len(e))])

    def impostor_scores(self) -> np.ndarray:
        if self.centroid is None or not len(self.impostors):
            return np.zeros(0)
        return self.impostors @ self.centroid

    def retune(self) -> dict:
        """Set reject and accept from the samples, and switch mode if the evidence says to."""
        loo = self.held_out()
        imp = self.impostor_scores()
        st = self.state
        if len(loo) < 5:
            st.update(reject=None, accept=None, why=f"{len(self.samples)} samples of him, not enough to set a line")
            if st.get("by") != "hand":
                st["mode"] = "shadow"
            return st
        reject = float(np.percentile(loo, 100 * (1 - PASS_RATE))) - MARGIN
        ceiling = float(np.max(imp)) if len(imp) else -1.0
        accept = max(reject + 0.05, min(ceiling + 0.03, float(np.median(loo))))
        wakes = sum(1 for s in self.samples if s["source"] != "enroll")
        passed = float(np.mean(loo >= reject))
        imp_rejected = float(np.mean(imp < reject)) if len(imp) else 0.0
        st.update(reject=round(reject, 3), accept=round(accept, 3), held_out_pass=round(passed, 3),
                  impostors_rejected=round(imp_rejected, 4), impostor_max=round(ceiling, 3),
                  samples=len(self.samples), wake_samples=wakes, tuned_at=time.time())
        ready = len(self.samples) >= ENFORCE_MIN and wakes >= ENFORCE_WAKES and passed >= PASS_RATE \
            and len(imp) and imp_rejected >= 0.99
        why = ("ready" if ready else
               f"{len(self.samples)}/{ENFORCE_MIN} samples, {wakes}/{ENFORCE_WAKES} from wakes"
               + (f", only {imp_rejected:.1%} of other voices fall under the line" if len(imp) and imp_rejected < 0.99
                  else "" if len(imp) else ", no test voices to check the line against"))
        st["why"] = why
        if st.get("by") != "hand":
            mode = "enforcing" if ready else "shadow"
            if mode != st.get("mode"):
                st.update(mode=mode, since=time.time(), by="auto")
        return st

    def set_mode(self, mode: str, by: str = "hand"):
        self.state.update(mode=mode, by=by, since=time.time())
        self.save()

    # ---- the record of every wake

    def keep_wake(self, audio: np.ndarray, meta: dict) -> str:
        """Every wake's audio and what was decided, the last WAKES_KEPT of them."""
        try:
            import soundfile
            stem = self.dir / "wakes" / f"{time.strftime('%Y%m%d-%H%M%S')}-{meta.get('qid', '')}"
            soundfile.write(stem.with_suffix(".wav"), np.asarray(audio, dtype=np.float32), RATE)
            stem.with_suffix(".json").write_text(json.dumps(meta, default=str))
            old = sorted((self.dir / "wakes").glob("*.wav"))[:-WAKES_KEPT]
            for p in old:
                p.unlink(missing_ok=True)
                p.with_suffix(".json").unlink(missing_ok=True)
            return stem.name
        except Exception:
            return ""

    def update_wake(self, name: str, **fields):
        p = self.dir / "wakes" / (name + ".json")
        try:
            meta = json.loads(p.read_text())
            meta.update(fields)
            p.write_text(json.dumps(meta, default=str))
        except (OSError, ValueError):
            pass

    def status(self) -> dict:
        st = self.state
        return {"mode": st.get("mode"), "set_by": st.get("by"), "samples": len(self.samples),
                "wake_samples": sum(1 for s in self.samples if s["source"] != "enroll"),
                "enrolled": sum(1 for s in self.samples if s["source"] == "enroll"),
                "candidates": len(self.candidates), "reject": st.get("reject"), "accept": st.get("accept"),
                "held_out_pass": st.get("held_out_pass"), "impostors_rejected": st.get("impostors_rejected"),
                "test_voices": len(self.impostors), "why": st.get("why")}


def _load_wav(path) -> np.ndarray:
    import soundfile
    import soxr
    x, r = soundfile.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    return soxr.resample(x, r, RATE) if r != RATE else x


def chunks(audio: np.ndarray, secs: float = 3.0) -> list:
    """Speech cut into stretches of about secs, each one sample."""
    x = speech_only(audio)
    n = int(secs * RATE)
    out = [x[i:i + n] for i in range(0, len(x), n)]
    if len(out) > 1 and len(out[-1]) < n * 0.6:
        out[-2] = np.concatenate([out[-2], out.pop()])
    return [c for c in out if len(c) >= RATE * 1.2]


def main(argv):
    sc = SpeakerCheck()
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(sc.status(), indent=1))
    elif cmd == "enroll":
        for p in argv[1:]:
            for c in chunks(_load_wav(p)):
                emb, secs = sc.embed(c)
                print(p, sc.add_sample(emb, {"seconds": round(secs, 2), "text": Path(p).name}, "enroll"))
        print(json.dumps(sc.status(), indent=1))
    elif cmd == "impostors":
        embs, names = [], []
        for d in argv[1:]:
            for p in sorted(Path(d).rglob("*.wav")):
                emb, _ = sc.embed(_load_wav(p))
                if emb is not None:
                    embs.append(emb)
                    names.append(str(p.relative_to(d)))
        np.savez(sc.dir / "impostors.npz", emb=np.stack(embs), names=np.array(names))
        sc.load()
        sc.retune()
        sc.save()
        print(len(embs), "test voices;", json.dumps(sc.status(), indent=1))
    elif cmd == "score":
        for p in argv[1:]:
            r = sc.check(_load_wav(p))
            print(f"{r['score']}\t{r['verdict']}\t{r['speech_s']}s\t{r['ms']}ms\t{p}")
    elif cmd == "retune":
        sc.retune()
        sc.save()
        print(json.dumps(sc.status(), indent=1))
    elif cmd == "mode" and len(argv) > 1 and argv[1] in ("shadow", "enforcing", "auto"):
        if argv[1] == "auto":
            sc.state["by"] = "auto"
            sc.retune()
            sc.save()
        else:
            sc.set_mode(argv[1])
        print(json.dumps(sc.status(), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
