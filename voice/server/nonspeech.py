#!/usr/bin/env python3
"""Whether what the microphones heard holds any words.

A cough, a throat clear, a click, a sneeze or a dish set down can set the wake word off, and
the follow-up window and the open ear hear them too. The recognizer turns a sound like that
into nothing, a filler ("Uh", "Hmm"), an annotation ("[cough]", "(coughs)"), a phrase it makes
up from noise ("Thank you."), or a short word it is not sure of. On 2026-10-06 it wrote his
coughs as "Yeah." and "Okay." with the word itself at a log probability of -1.4 to -2.1, where
the words he actually said sit between 0 and -0.4. None of that is said to Sunday: it never
reaches Claude, chimes, lights the ring or shows on the popup.

The test is deterministic. Lowercase the transcript, take out bracketed and parenthesized
annotations, punctuation and filler sounds, and at least one real word must be left. A lone
sound word ("cough"), a lone function word ("the") and the phrases a recognizer makes up from
noise do not count. Given how sure the recognizer was of each word, at least one real word must
also be one it was sure of, except a stop or a mute, which always counts. Single words that
matter survive: "stop", "yes", "no", "mute", "Sunday" and numbers.

    python3 nonspeech.py selftest     # the text cases; where the recognizer is (the Pi), the audio ones too
"""

import re
import sys
from pathlib import Path

RATE = 16000
# A word the recognizer was sure of: its least certain piece at a log probability of at least
# this. His words on 2026-10-06 sat between 0 and -0.4 (a word in a garbled stretch heard over
# its own voice reached -1.9); the words it made up from his coughs sat at -1.4 to -2.1.
SURE = -1.0

FILLER_SOUNDS = {
    "uh", "uhh", "uhm", "um", "umm", "erm", "er", "err", "ah", "ahh", "aah", "eh", "hm", "hmm", "hmmm",
    "mm", "mmm", "mhm", "mmhmm", "mmhm", "hmph", "huh", "oh", "ooh", "ohh", "aw", "ow", "ugh", "ha", "hah",
    "haha", "hahaha", "heh", "pfft", "psst", "shh", "tsk", "achoo", "ahem", "whew", "phew",
}
# Words that only name a sound. With nothing else, the transcript is the sound.
SOUND_WORDS = {
    "cough", "coughs", "coughing", "sneeze", "sneezes", "sneezing", "laugh", "laughs", "laughing",
    "laughter", "sigh", "sighs", "sniff", "sniffs", "sniffing", "throat", "clears", "clearing", "music",
    "noise", "noises", "applause", "silence", "static", "inaudible", "blank", "audio", "background",
    "breath", "breathing", "click", "clicks", "clicking", "clatter", "clattering", "beep", "beeps",
}
# Not a request by itself.
LONE = {"a", "an", "the", "and", "but", "or", "so", "i", "it", "is", "to", "of", "in", "on", "at",
        "for", "with", "that", "this", "like", "well", "just"}
# Whole transcripts a recognizer makes up from noise.
MADE_UP = {
    "you", "thank you", "thank you so much", "thank you very much", "thanks", "thanks a lot",
    "thanks for watching", "thank you for watching", "thanks for listening", "thank you for listening",
    "please subscribe", "subscribe", "like and subscribe", "bye", "bye bye", "see you", "see you next time",
    "the end",
}
# Words that always count, sure or not: a plain stop or mute always acts.
COMMANDS = {"stop", "mute", "unmute", "quiet", "cancel", "pause"}
# The wake word as the recognizer writes it.
SUNDAY_LIKE = re.compile(r"^(?:sunday|sundays|sunday's|sundae|sundaes|someday|sonday|sundy)$")

_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|<[^>]*>|\*[^*]*\*")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def words(text: str) -> list:
    """The words of a transcript: lowercased, with annotations and punctuation gone."""
    return _WORD.findall(_ANNOTATION.sub(" ", (text or "").lower()).replace("’", "'"))


def real_words(text: str) -> list:
    """The words left once filler sounds are out, or [] when none of them is a real word."""
    ws = [w for w in words(text) if w not in FILLER_SOUNDS]
    if not ws or all(w in SOUND_WORDS for w in ws) or all(w in LONE for w in ws):
        return []
    if " ".join(ws) in MADE_UP or " ".join(dict.fromkeys(ws)) in MADE_UP:
        return []
    return ws


def scored_words(tokens, logprobs) -> list:
    """(word, how sure) for each word of a recognizer result: its pieces are joined, and a word
    is as sure as its least certain piece. Punctuation pieces say nothing about the word."""
    out, cur, worst = [], "", None
    for tok, lp in zip(tokens or [], logprobs or []):
        if tok.startswith(" ") and cur:
            out.append((cur, worst))
            cur, worst = "", None
        cur += tok.strip() if not cur else tok
        if re.search(r"[A-Za-z0-9]", tok):
            worst = lp if worst is None else min(worst, lp)
    if cur:
        out.append((cur, worst))
    return [(w, c) for word, c in out for w in words(word) if c is not None]


def junk(text: str, scored: list | None = None) -> str:
    """Why this transcript holds no words said to Sunday, or "" when it does."""
    if not (text or "").strip():
        return "nothing heard"
    ws = words(text)
    if not ws:
        return "only annotations or punctuation"
    real = real_words(text)
    if not real:
        rest = [w for w in ws if w not in FILLER_SOUNDS]
        if not rest:
            return "only filler sounds"
        if all(w in SOUND_WORDS for w in rest):
            return "only the name of a sound"
        if all(w in LONE for w in rest):
            return "no word that asks for anything"
        return "a phrase the recognizer makes up from noise"
    if any(w in COMMANDS for w in real):
        return ""
    if scored:
        keep = set(real)
        if not any(c >= SURE for w, c in scored if w in keep):
            return "no word the recognizer was sure of (" + ", ".join(f"{w} {c:.1f}" for w, c in scored) + ")"
    return ""


def _joined(pairs: list) -> list:
    """(word, how sure) with "some day" and "sun day" made one word, as sure as its less
    certain half: his "Sunday" from across the room at the Mac came back as "Some day."."""
    out, i = [], 0
    while i < len(pairs):
        if i + 1 < len(pairs) and pairs[i][0] in ("some", "sun") and pairs[i + 1][0] == "day":
            a, b = pairs[i][1], pairs[i + 1][1]
            out.append((pairs[i][0] + "day", None if a is None else min(a, b)))
            i += 2
        else:
            out.append(pairs[i])
            i += 1
    return out


def wake_word_heard(text: str, scored: list | None = None) -> bool:
    """The wake word itself is in this audio, and the recognizer was sure of it: a real
    "Sunday", not a cough the wake word took for one."""
    if scored:
        return any(SUNDAY_LIKE.match(w) and c >= SURE for w, c in _joined(scored))
    return any(SUNDAY_LIKE.match(w) for w, _ in _joined([(w, None) for w in words(text)]))


def recognize(asr, samples) -> tuple:
    """(text, scored words) for these samples, levelled the way every transcript here is."""
    import numpy as np
    samples = np.asarray(samples, dtype=np.float32)
    if not len(samples):
        return "", []
    samples = samples - samples.mean()
    samples = samples * min(0.8 / (float(np.abs(samples).max()) or 1.0), 30.0)
    s = asr.create_stream()
    s.accept_waveform(RATE, samples)
    asr.decode_stream(s)
    r = s.result
    return r.text.strip(), scored_words(r.tokens, list(getattr(r, "ys_log_probs", []) or []))


# ---------------------------------------------------------------- selftest

# (transcript, junk?) - the cough cases are what the recognizer made of his real coughs, and
# what Whisper-style recognizers are known to make up from noise.
TEXT_CASES = [
    ("", True), ("   ", True), (".", True), ("...", True), ("♪ ♫", True), ("♪", True),
    ("Uh", True), ("Uh.", True), ("Um", True), ("Hmm.", True), ("Hmm...", True), ("Mm-hmm.", True),
    ("Uh-huh.", True), ("Uh, um.", True), ("Oh.", True), ("Ah!", True), ("Huh?", True), ("Ahem.", True),
    ("[cough]", True), ("(coughs)", True), ("(coughing)", True), ("[COUGH]", True), ("*cough*", True),
    ("[clears throat]", True), ("(sneezes)", True), ("[music]", True), ("[BLANK_AUDIO]", True),
    ("(inaudible)", True), ("[laughter]", True), ("(sighs) Hmm.", True), ("Cough.", True),
    ("Cough cough.", True), ("Coughing.", True), ("you", True), ("You.", True), ("you you", True),
    ("Thank you.", True), ("Thank you!", True), ("Thanks.", True), ("Thanks for watching!", True),
    ("Thank you for watching.", True), ("Bye.", True), ("Bye bye.", True), ("The end.", True),
    ("So.", True), ("The.", True), ("And the.", True), ("I.", True), ("Like, uh.", True),
    ("Well, um.", True), ("♪ Thank you. ♪", True), ("[cough] Uh.", True),
    # real words that must pass
    ("Stop.", False), ("stop", False), ("Stop!", False), ("Yes.", False), ("Yes", False), ("No.", False),
    ("no", False), ("Mute.", False), ("Unmute.", False), ("Sunday.", False), ("Sunday", False),
    ("Sunday?", False), ("28", False), ("Seven.", False), ("1,249.", False), ("Yeah.", False),
    ("Okay.", False), ("Status.", False), ("Quiet.", False), ("Cancel.", False), ("Wait.", False),
    ("Uh, stop.", False), ("[cough] Stop.", False), ("(coughs) Sunday, what time is it?", False),
    ("Sunday, what time is it?", False), ("What time is it?", False), ("Hey, oh, oh.", False),
    ("You're activating if I cough.", False), ("Thank you, Sunday.", False), ("Thanks, what's next?", False),
    ("Never mind.", False), ("Shut up.", False), ("Go ahead.", False), ("Nothing.", False),
    ("I didn't say anything.", False), ("Tell me a story.", False), ("Someday.", False),
]

# (transcript, scored words, junk?) - how sure the recognizer was decides the short ones.
SCORED_CASES = [
    ("Yeah.", [("yeah", -2.11)], True), ("Okay.", [("okay", -2.03)], True), ("Yeah.", [("yeah", -1.36)], True),
    ("Yeah.", [("yeah", -0.14)], False), ("Okay.", [("okay", -0.2)], False), ("No.", [("no", -0.05)], False),
    ("Stop.", [("stop", -2.5)], False), ("Mute.", [("mute", -1.8)], False),
    ("Sunday.", [("sunday", -0.06)], False), ("Sunday.", [("sunday", -2.4)], True),
    ("Uh, yeah.", [("uh", -0.4), ("yeah", -1.6)], True),
    ("I don't think you answered me.", [("i", -0.01), ("don't", 0.0), ("think", 0.0), ("you", 0.0),
                                        ("answered", 0.0), ("me", -0.33)], False),
]

WAKE_CASES = [
    ("Sunday.", [("sunday", -0.06)], True), ("Sunday", None, True), ("yourself Sunday", None, True),
    ("Someday.", [("someday", -0.22)], True), ("", [], False), ("Yeah.", [("yeah", -2.11)], False),
    ("Okay.", [("okay", -2.03)], False), ("Um", [("um", -0.95)], False), ("Monday.", None, False),
    ("Sunday.", [("sunday", -2.3)], False), ("Sun.", None, False),
    ("Some day.", [("some", -0.80), ("day", -0.03)], True), ("Some day.", None, True),
    ("Some day.", [("some", -1.6), ("day", -0.03)], False), ("Some of the day.", None, False),
]


# Whether the second before the wake fired holds its name: his "Sunday", where it was there.
# On 222704-cough-first the cough woke it and he said "Sunday" after.
PREROLL_NAMED = {"wake-222452-answered.wav": True, "wake-222704-mac.wav": True}


def _clips() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "nonspeech"


def _load_asr():
    """The recognizer voice_server.py runs, if this machine has it."""
    try:
        import sherpa_onnx
    except ImportError:
        return None
    d = Path(__file__).resolve().parent / "models" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
    if not d.is_dir():
        return None
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"), joiner=str(d / "joiner.int8.onnx"),
        tokens=str(d / "tokens.txt"), model_type="nemo_transducer", num_threads=4)


def _read_wav(path: Path):
    import wave
    import numpy as np
    with wave.open(str(path)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def _synthetic() -> list:
    """Sounds with no words, made here: clicks, a clatter, a hum, a knock, room noise."""
    import numpy as np
    rng = np.random.default_rng(7)
    n = RATE * 2
    clicks = np.zeros(n, np.float32)
    for at in (2000, 9000, 15500, 23000):
        clicks[at:at + 40] = rng.uniform(-0.6, 0.6, 40)
    clatter = np.zeros(n, np.float32)
    for at in (3000, 4100, 5600, 7900, 8300):
        burst = rng.normal(0, 0.3, 1600).astype(np.float32) * np.exp(-np.arange(1600) / 250)
        clatter[at:at + 1600] += burst
    t = np.arange(n) / RATE
    hum = (0.2 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    knock = np.zeros(n, np.float32)
    for at in (4000, 8000):
        knock[at:at + 800] = (0.5 * np.sin(2 * np.pi * 90 * t[:800]) * np.exp(-np.arange(800) / 150)).astype(np.float32)
    noise = rng.normal(0, 0.02, n).astype(np.float32)
    return [("synthetic clicks", clicks), ("synthetic clatter", clatter), ("synthetic hum", hum),
            ("synthetic knock", knock), ("synthetic room noise", noise)]


def selftest() -> int:
    bad = 0
    for text, want in TEXT_CASES:
        why = junk(text)
        if bool(why) != want:
            bad += 1
            print(f"FAIL text {text!r}: {'dropped (' + why + ')' if why else 'kept'}, wanted {'dropped' if want else 'kept'}")
    for text, scored, want in SCORED_CASES:
        why = junk(text, scored)
        if bool(why) != want:
            bad += 1
            print(f"FAIL scored {text!r} {scored}: {'dropped (' + why + ')' if why else 'kept'}")
    for text, scored, want in WAKE_CASES:
        if wake_word_heard(text, scored) != want:
            bad += 1
            print(f"FAIL wake word {text!r} {scored}: wanted {want}")
    n = len(TEXT_CASES) + len(SCORED_CASES) + len(WAKE_CASES)
    print(f"text: {n - bad} of {n} passed")
    # Real and made audio through the recognizer, where it runs. cough-*: his coughs that woke
    # it on 2026-10-06, and the made sounds: no words, and never its name. wake-*: "Sunday" and
    # what he said after, kept, with its name heard. word-*: words without its name, kept.
    asr = _load_asr()
    if asr is None:
        print("audio: skipped (the recognizer is not on this machine; run it on the Pi)")
        return 1 if bad else 0
    clips = [(p.name, _read_wav(p)) for p in sorted(_clips().glob("*.wav"))] + _synthetic()
    abad = 0
    for name, audio in clips:
        text, scored = recognize(asr, audio)
        why = junk(text, scored)
        wake = wake_word_heard(text, scored)
        kept, named = name.startswith(("wake-", "word-")), name.startswith("wake-")
        ok = (not why) == kept and wake == named
        abad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {text!r} -> {'kept' if not why else 'dropped (' + why + ')'}"
              f"{', its name heard' if wake else ''}")
    # The second before each real wake fired, as the brain checks it before anything chimes:
    # the recognizer wrote some of these coughs as "Yeah." or "Okay.", never as its name.
    pbad, pn = 0, 0
    for name, audio in clips:
        if not name.startswith(("cough-", "wake-2")):
            continue
        pn += 1
        text, scored = recognize(asr, audio[RATE // 2: RATE * 3 // 2])
        named = wake_word_heard(text, scored)
        ok = named == PREROLL_NAMED.get(name, False)
        pbad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}, the second before the wake: {text!r}"
              f"{' (' + ', '.join(f'{w} {c:.1f}' for w, c in scored) + ')' if scored else ''}"
              f" -> {'its name heard' if named else 'no name'}")
    print(f"audio: {len(clips) - abad} of {len(clips)} passed; the second before the wake: {pn - pbad} of {pn} passed")
    return 1 if bad or abad or pbad else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(selftest())
    for line in sys.argv[1:]:
        print(f"{line!r}: {junk(line) or 'kept'}")
