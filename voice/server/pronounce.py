#!/usr/bin/env python3
"""The pronunciation pass: the last thing done to a clause before a voice says it.

A heteronym is spelled one way and said two ("the site is live" against "where you
live"), and neither voice can be trusted to pick the right one: Kokoro has one entry per
word in its lexicon and never looks at the sentence, and ElevenLabs guesses from context
and gets the short words wrong. So the sense is read here from the words around it, and
the word is respelled into something each engine can only say one way. The respelling
goes to the voice alone; the popup, the logs and the speech gate see the text as written.

RULES is the whole table. Each row is a name, a pattern whose group `w` is the word to
change, and what each engine is given in its place. None leaves the word to the engine,
which is how a row claims a word for one sense and stops a later row from taking it for
the other. Rows are tried in order and the first to match a word owns it, so the narrow
contexts go above the broad defaults. `unless` is a pattern the text before the word
must not end with.

Kokoro (sherpa-onnx) turns English into phonemes with espeak-ng over the whole sentence,
so its respellings were chosen against espeak-ng: each one is a spelling espeak can only
read one way. Where espeak already reads a word right in every context (the letter names,
GitHub, Supabase), the Kokoro column is None, and the same goes for ElevenLabs where
its own reading was checked and found right (npm, API, Vercel, Supabase, GitHub); those
rows stay so the word is claimed and the next person can see it was looked at. ElevenLabs flash v2.5 drops a word wrapped
in an SSML phoneme tag rather than saying it, so respelling is the only lever there.

    python3 pronounce.py check                   # the table against its expected outputs
    python3 pronounce.py say "The site is live now."
"""
import re
import sys
from pathlib import Path

try:
    import mtoa_config
except ImportError:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "mtoa_config", str(Path(__file__).resolve().parent / "mtoa_config.py"))
    mtoa_config = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mtoa_config)

I = re.I


def _own_rules():
    """The owner's own names, from the profile's voice.pronounce: each a name, a pattern
    whose group `w` is the word, and what it is said as (one string for both engines, or
    {"eleven": ..., "kokoro": ...}), with an optional `unless` and `example`."""
    rows = []
    for rule in mtoa_config.get("voice.pronounce") or []:
        try:
            say = rule["say"]
            by = dict(say) if isinstance(say, dict) else {"eleven": say, "kokoro": say}
            re.compile(rule["pattern"])
            rows.append((rule["name"], rule["pattern"], rule.get("unless"), by))
        except (KeyError, TypeError, re.error):
            continue
    return rows


def _own_cases():
    """Each profile rule's example: the sentence, and what both engines must be given (or
    a third item for Kokoro where it differs)."""
    cases = []
    for rule in mtoa_config.get("voice.pronounce") or []:
        ex = rule.get("example") if isinstance(rule, dict) else None
        if isinstance(ex, list) and len(ex) in (2, 3):
            cases.append((ex[0], ex[1], ex[-1]))
    return cases

# Words that, standing right before "live", make it the adjective: it is live, went
# live, put it live. Up to two filler words may sit between ("is now live", "put it live").
_LIVE_BEFORE = (r"(?:\b(?:is|are|was|were|be|been|being|isn't|aren't|wasn't|weren't|go|goes|going|went|gone|"
                r"put|puts|putting|push|pushes|pushed|pushing|take|takes|took|taking|taken|make|makes|"
                r"made|making|stay|stays|stayed|staying|turn|turned|switch|switched)|'s|'re)"
                r"\s+(?:(?:it|this|that|them|now|already|finally|actually|officially|fully|still|not|"
                r"currently|back|properly|successfully|really|all|both|also|just|then)\s+){0,2}")
# Nouns "live" describes: a live site, live data, live mode.
_LIVE_NOUNS = (r"site|sites|version|versions|page|pages|data|demo|demos|feed|feeds|stream|streams|streaming|"
               r"server|servers|build|builds|deploy|deploys|deployment|deployments|url|urls|link|links|app|"
               r"apps|copy|environment|env|traffic|preview|product|products|chat|event|events|update|updates|"
               r"status|view|database|db|system|domain|domains|endpoint|endpoints|key|keys|mode|music|show|"
               r"shows|broadcast|call|calls|session|sessions|transcript|transcripts|tickets?|tests?|run|runs|"
               r"wire|audio|video|captions|logs?|branch|release|releases|instance|api|dashboard|numbers|"
               r"price|prices|pricing|checkout|payments?|account|accounts|forms?|reload|coverage|tv|radio|"
               r"band|performance|preview|previews|setup|config|configuration|traffic|users|customers|"
               r"version|cube|feed|counter|count|stats|clock|edit|edits|changes")

RULES = [
    # ---- live: long i (rhymes with five) when it means online or happening now
    ("live: before a noun", rf"\b(?P<w>live)(?=[\s-]+(?:{_LIVE_NOUNS})\b)", None,
     {"eleven": "lyve", "kokoro": "lyve"}),
    ("live: after is, went, put it", rf"{_LIVE_BEFORE}(?P<w>live)\b", r"\blet",
     {"eleven": "lyve", "kokoro": "lyve"}),
    # ---- live: short i when it is the verb, which Kokoro's lexicon never says
    ("live: the verb, after its subject", r"(?:\b(?:I|you|we|they|people|folks|who|to|do|does|did|don't|doesn't|"
     r"didn't|can|can't|cannot|could|couldn't|will|won't|would|wouldn't|should|shouldn't|might|must|may|ever|"
     r"where)|'d|'ll)\s+(?P<w>live)\b", None, {"eleven": None, "kokoro": "liv"}),
    ("live: the verb, before with, in, by", r"\b(?P<w>live)(?=\s+(?:with|by|and\s+learn|off|long|like|through|"
     r"here|there|nearby|alone|together|forever|on|at|under|inside|in\s+(?!production\b|prod\b)))", None,
     {"eleven": None, "kokoro": "liv"}),
    ("live: before now, yet, in production", r"\b(?P<w>live)(?=\s+(?:now|yet|again|already|today|tonight|"
     r"right\s+now|in\s+(?:production|prod)\b|for\s+(?:everyone|customers|users|visitors)))", None,
     {"eleven": "lyve", "kokoro": "lyve"}),
    ("live: ending the sentence", r"\b(?P<w>live)(?=\s*[.!?]?\s*$)", None, {"eleven": "lyve", "kokoro": "lyve"}),
    ("lives: the verb", r"(?:\b(?:he|she|it|who|that|which|one|everyone|someone|everything|still|now)\s+)(?P<w>lives)\b"
     r"|\b(?P<w2>lives)(?=\s+(?:in|at|on|with|under|inside|here|there)\b)", None, {"eleven": None, "kokoro": "livz"}),

    # ---- read: "red" in the past tense and past participle
    ("read: after have, is, already", r"(?:\b(?:have|has|had|having|was|were|been|being|is|are|be|am|isn't|wasn't|"
     r"aren't|weren't|hasn't|haven't|hadn't|already|never|recently|once)|'ve|'s)\s+(?:(?:not|already|just|"
     r"never|all|both|it|them|through|also|finally)\s+){0,2}(?P<w>read)\b", r"\blet",
     {"eleven": "red", "kokoro": "red"}),
    ("read: with a time in the past", r"\b(?:I|you|we|they|he|she|it|Sunday)\s+(?:just\s+|already\s+|also\s+|then\s+|"
     r"finally\s+)?(?P<w>read)\b(?=[^.!?]*\b(?:ago|yesterday|earlier|last\s+(?:night|week|month|time|year)|"
     r"this\s+morning|before\s+you|just\s+now|already)\b)", None, {"eleven": "red", "kokoro": "red"}),
    ("read: I read the, I read it", r"\bI\s+(?:just\s+|already\s+|also\s+|then\s+|finally\s+)?(?P<w>read)\b"
     r"(?=\s+(?:your|the|it|that|this|them|through|over|all|both|every|his|her|their|my|a|an|those|these|"
     r"what|everything)\b)(?![^.!?]*\b(?:every|each|always|usually|daily|whenever)\b)",
     r"\b(?:should|shall|can|could|do|will|would|may|might|must|did|if|when|whenever|before|after|until|"
     r"once|let|want|need|while|as)\s*$", {"eleven": "red", "kokoro": "red"}),

    # ---- lead: "led" only for the metal; a sales lead and to lead stay "leed"
    ("lead: the metal", r"\b(?P<w>lead)(?=[\s-]+(?:pipes?|paint|poisoning|based|free|levels?|contamination|"
     r"in\s+the\s+water|shot|crystal|apron|weights?)\b)", None, {"eleven": "led", "kokoro": "led"}),

    # ---- close: "cloze" as the verb; Kokoro's lexicon only has the adjective
    ("close: the verb", r"\b(?P<w>close)(?=\s+(?:the|it|that|this|them|those|these|out|down|my|your|his|her|our|"
     r"their|every|any|some|all|issues?|tickets?|tabs?|PRs?|pull\s+requests?|windows?|sessions?|branch(?:es)?|"
     r"accounts?|deals?|gaps?|loops?)\b)",
     r"\b(?:very|so|too|pretty|really|quite|that|this|how|as|getting|get|up|fairly|super|real|is|are|was|were|"
     r"be|stay|come|came)\s*$", {"eleven": None, "kokoro": "cloze"}),

    # ---- wound: "wownd" as the past of wind; the injury stays "woond"
    ("wound: past of wind", r"\b(?P<w>wound)(?=\s+(?:up|down|around|round|it|the|them|tight|back|through)\b)",
     r"\b(?:a|the|this|that|his|her|my|your|open|gunshot|stab|knife|old)\s*$",
     {"eleven": "wownd", "kokoro": "wownd"}),

    # ---- tear: "tare" for rip; a tear from an eye stays "teer"
    ("tear: rip", r"\b(?P<w>tear)(?=\s+(?:it|that|this|them|down|up|apart|off|out|through|into|open|away|"
     r"the|a|an|my|your|his|her|our|their)\b)", r"\b(?:a|single|one|the)\s*$",
     {"eleven": "tare", "kokoro": "tare"}),
    ("tear: wear and tear", r"\bwear\s+and\s+(?P<w>tear)\b", None, {"eleven": "tare", "kokoro": "tare"}),
    ("tears: rips", r"\b(?P<w>tears)(?=\s+(?:it|that|this|them|down|up|apart|off|out|through|into|open)\b)",
     r"\b(?:in|of|the|my|your|her|his|no|into|to)\s*$", {"eleven": "tares", "kokoro": "tares"}),

    # ---- minute: "my newt" only when it means tiny
    ("minute: tiny", r"\b(?P<w>minute)(?=\s+(?:details?|amounts?|differences?|changes?|fractions?|adjustments?|"
     r"particles?|quantities|quantity|traces?|variations?|errors?)\b)",
     r"\b(?:one|every|each|per|last|next|first|\d+|two|three|four|five|ten|fifteen|thirty)\s*$",
     {"eleven": "my-noot", "kokoro": "my newt"}),

    # ---- stress pairs. ElevenLabs reads these from context; Kokoro has one stress per word.
    ("record: the verb", r"(?:\b(?:to|will|can|could|would|should|must|might|please|then|we|I|you|they|don't|"
     r"didn't|won't|can't|also|always|never)|'ll)\s+(?:also\s+|just\s+)?(?P<w>record)\b"
     r"|\b(?P<w2>record)(?=\s+(?:it|that|this|them|everything|what|audio|video|your|my|a\s+(?:video|clip|call|note|"
     r"memo)|the\s+(?:call|meeting|screen|audio|video))\b)",
     r"\b(?:a|the|on|off|your|my|our|their|his|her|world|new|track|criminal|public|permanent|for\s+the)\s*$",
     {"eleven": None, "kokoro": "rekord"}),
    ("present: the verb", r"(?:\b(?:to|will|can|could|would|should|must|might|please|then|we|I|you|they)|'ll)\s+"
     r"(?P<w>present)\b(?!\s+(?:day|tense|moment|time|value|state|company))"
     r"|\b(?P<w2>present)(?=\s+(?:it|this|that|them|these|those|your|my|our|options|findings|results|"
     r"the\s+(?:findings|results|options|plan|deck|proposal|idea|numbers))\b)",
     r"\b(?:the|at|is|are|was|were|be|all|for|until|up\s+to|everyone|nobody)\s*$",
     {"eleven": None, "kokoro": "prezent"}),
    ("content: the adjective", r"\b(?:am|is|are|was|were|be|been|being|feel|feels|felt|seem|seems|seemed|remain|"
     r"remains|stay|stays|perfectly|quite|very|fairly|not)\s+(?P<w>content)(?=\s+(?:with|to|that|enough)\b|"
     r"\s*[.,!?]|\s*$)", None, {"eleven": None, "kokoro": None}),
    ("content: the noun", r"\b(?P<w>content)\b", None, {"eleven": None, "kokoro": "kontent"}),
    ("object: the verb", r"\b(?:I|you|we|they|he|she|would|might|may|will|could|to|don't|didn't|won't|doesn't)\s+"
     r"(?P<w>object)(?=\s+(?:to|if|that|strongly)\b|\s*[.,!?]|\s*$)", None, {"eleven": None, "kokoro": "objekt"}),
    ("produce: the noun", r"\b(?:the|fresh|local|organic|some|their|farm)\s+(?P<w>produce)\b"
     r"(?!\s+(?:a|an|the|it|results?|reports?|content|more|less|this|that)\b)"
     r"|\b(?P<w2>produce)(?=\s+(?:section|aisle|stand|department|market|delivery)\b)", None,
     {"eleven": None, "kokoro": "proedoose"}),
    ("desert: to abandon", r"\b(?:to|will|would|never|not|won't|didn't|don't)\s+(?P<w>desert)\b", None,
     {"eleven": None, "kokoro": "dessert"}),

    # ---- vowel pairs Kokoro's lexicon holds one side of
    ("bass: the fish", r"\b(?:sea|striped|largemouth|smallmouth|black|white|spotted|peacock|rock|kelp|chilean)\s+"
     r"(?P<w>bass)\b|\b(?P<w2>bass)(?=\s+(?:fishing|fish|boat|lake|pond|lures?|tournament)\b)", None,
     {"eleven": None, "kokoro": None}),
    ("bass: the sound", r"\b(?P<w>bass)\b", None, {"eleven": None, "kokoro": "base"}),
    ("row: an argument", r"\b(?:had|have|having|got\s+into|get\s+into)\s+a\s+(?:big\s+|huge\s+|bit\s+of\s+a\s+)?"
     r"(?P<w>row)(?=\s+(?:with|about|over)\s+(?:him|her|them|me|us|you)\b)", None,
     {"eleven": "rau", "kokoro": "rau"}),
    ("wind: the verb", r"\b(?P<w>wind)(?=\s+(?:up|down|back|it|the\s+clock|the\s+watch|around|round|through|"
     r"its\s+way|their\s+way|our\s+way|things\s+down)\b)",
     r"\b(?:the|a|of|north|south|east|west|cold|strong|high|gusty|light|warm|no|any|some)\s*$",
     {"eleven": None, "kokoro": "wined"}),
    ("use: the noun", r"\b(?:the|a|no|any|in|of|for|its|your|my|our|their|his|her|much|fair|good|best|better|"
     r"heavy|daily|personal|commercial|internal|common|every|each|single|full|first|one|limited|regular|"
     r"main|real|practical)\s+(?P<w>use)\b(?!\s+(?:it|this|that|them|the|a|an|your|my)\b)"
     r"|\b(?P<w2>use)(?=[\s-]+cases?\b)", None, {"eleven": None, "kokoro": "yoose"}),

    # ---- names and letters that read wrong as words
    *_own_rules(),
    ("URLs", r"\b(?P<w>URLs)\b", None, {"eleven": "U R Ls", "kokoro": None}),
    ("URL", r"\b(?P<w>URL|url)\b", None, {"eleven": "U R L", "kokoro": None}),
    ("UI", r"\b(?P<w>UI)\b", None, {"eleven": "U I", "kokoro": None}),
    ("MySQL", r"\b(?P<w>MySQL)\b", None, {"eleven": "My sequel", "kokoro": "My sequel"}),
    ("PostgreSQL", r"\b(?P<w>PostgreSQL)\b", None, {"eleven": "Postgres", "kokoro": "Postgres"}),
    ("SQLite", r"\b(?P<w>SQLite)\b", None, {"eleven": "sequel light", "kokoro": "sequel light"}),
    ("SQL", r"\b(?P<w>SQL)\b", None, {"eleven": "sequel", "kokoro": "sequel"}),
    ("npm", r"\b(?P<w>npm|NPM)\b", None, {"eleven": None, "kokoro": None}),
    ("APIs", r"\b(?P<w>APIs)\b", None, {"eleven": None, "kokoro": None}),
    ("API", r"\b(?P<w>API)\b", None, {"eleven": None, "kokoro": None}),
    ("dBFS", r"\b(?P<w>dBFS)\b", None, {"eleven": "D B F S", "kokoro": None}),
    ("dB", r"(?<=\d)\s?(?P<w>dB)\b|\b(?P<w2>dB)\b", None, {"eleven": " D B", "kokoro": None}),
    ("PRs", r"\b(?P<w>PRs)\b", None, {"eleven": "P Rs", "kokoro": None}),
    ("PR", r"\b(?P<w>PR)\b", None, {"eleven": "P R", "kokoro": None}),
    ("dot com", r"(?<=\w)(?P<w>\.(?:com|io|dev|app|net|org))\b", None,
     {"eleven": lambda w: " dot " + w[1:], "kokoro": lambda w: " dot " + w[1:]}),
    ("dot ai", r"(?<=\w)(?P<w>\.ai)\b", None, {"eleven": " dot A I", "kokoro": " dot A I"}),
    ("Vercel", r"\b(?P<w>Vercel)\b", None, {"eleven": None, "kokoro": None}),
    ("Supabase", r"\b(?P<w>Supabase)\b", None, {"eleven": None, "kokoro": None}),
    ("GitHub", r"\b(?P<w>GitHub|Github|github)\b", None, {"eleven": None, "kokoro": None}),
    ("repo", r"\b(?P<w>repos?)\b", None, {"eleven": None, "kokoro": None}),
]

# Word rows match without case, so "Live" and "live" are one word. Letter rows ("UI",
# "PR", "dB") and product names match only as written, so "pr" or "ui" inside a sentence
# that happens to be lowercase never does.
_AS_WRITTEN = {"URLs", "URL", "UI", "MySQL", "PostgreSQL", "SQLite", "SQL", "npm", "APIs", "API", "dBFS",
               "dB", "PRs", "PR", "Vercel", "Supabase", "GitHub"}
_COMPILED = [(name, re.compile(pat, 0 if name in _AS_WRITTEN else I),
              re.compile(unless + r"\s*$", I) if unless else None, by)
             for name, pat, unless, by in RULES]


def _cased(new: str, old: str) -> str:
    # "Live" -> "Lyve", but an acronym's capital is not a sentence's: "SQL" -> "sequel".
    if old[:1].isupper() and new[:1].islower() and (len(old) == 1 or old[1:2].islower()):
        return new[:1].upper() + new[1:]
    return new


def plan(text: str, engine: str) -> list[tuple[int, int, str, str]]:
    """Every word the table claims in text: (start, end, respelling or None, row name)."""
    claimed: list[tuple[int, int, str | None, str]] = []
    for name, rx, unless, by in _COMPILED:
        for m in rx.finditer(text):
            g = "w" if m.group("w") is not None else "w2"
            s, e = m.span(g)
            if any(s < ce and cs < e for cs, ce, _, _ in claimed):
                continue
            if unless is not None and unless.search(text[:m.start()]):
                continue
            new = by.get(engine)
            if callable(new):
                new = new(m.group(g))
            claimed.append((s, e, None if new is None else _cased(new, m.group(g)), name))
    return sorted(claimed)


def respell(text: str, engine: str, context: str | None = None, at: int = 0) -> str:
    """text as engine should be given it. context is the whole sentence text was cut from,
    starting at offset at, so a clause is read with the words around it."""
    if context is None or context[at:at + len(text)] != text:
        context, at = text, 0
    out, pos = [], at
    for s, e, new, _ in plan(context, engine):
        if new is None or s < at or e > at + len(text):
            continue
        out.append(context[pos:s])
        out.append(new)
        pos = e
    out.append(context[pos:at + len(text)])
    return re.sub(r"  +", " ", "".join(out))


# ---- the table's own test: each sentence and what each engine must be given

CASES = [
    ("The site is live now.", "The site is lyve now.", "The site is lyve now."),
    ("It went live.", "It went lyve.", "It went lyve."),
    ("Where do you live?", "Where do you live?", "Where do you liv?"),
    ("The live site is up.", "The lyve site is up.", "The lyve site is up."),
    ("We put it live this morning.", "We put it lyve this morning.", "We put it lyve this morning."),
    *_own_cases(),
    ("Switch to live mode.", "Switch to lyve mode.", "Switch to lyve mode."),
    ("Those docs live in the repo.", "Those docs live in the repo.", "Those docs liv in the repo."),
    ("The repos live on GitHub.", "The repos live on GitHub.", "The repos liv on GitHub."),
    ("It's live on Vercel.", "It's lyve on Vercel.", "It's lyve on Vercel."),
    ("You can live with that.", "You can live with that.", "You can liv with that."),
    ("Is the new version live?", "Is the new version lyve?", "Is the new version lyve?"),
    ("Is it live yet?", "Is it lyve yet?", "Is it lyve yet?"),
    ("I read your inbox a minute ago.", "I red your inbox a minute ago.", "I red your inbox a minute ago."),
    ("I read it a minute ago.", "I red it a minute ago.", "I red it a minute ago."),
    ("I've read all of them.", "I've red all of them.", "I've red all of them."),
    ("Should I read it to you?", "Should I read it to you?", "Should I read it to you?"),
    ("I read every message each morning.", "I read every message each morning.",
     "I read every message each morning."),
    ("Want me to read it?", "Want me to read it?", "Want me to read it?"),
    ("Let's read through it.", "Let's read through it.", "Let's read through it."),
    ("It's read every night.", "It's red every night.", "It's red every night."),
    ("Three new leads came in.", "Three new leads came in.", "Three new leads came in."),
    ("The old lead pipes are gone.", "The old led pipes are gone.", "The old led pipes are gone."),
    ("Should I close the ticket?", "Should I close the ticket?", "Should I cloze the ticket?"),
    ("We're close to done.", "We're close to done.", "We're close to done."),
    ("It wound up on main.", "It wownd up on main.", "It wownd up on main."),
    ("Don't tear it down.", "Don't tare it down.", "Don't tare it down."),
    ("It was a minute difference.", "It was a my-noot difference.", "It was a my newt difference."),
    ("Give me a minute.", "Give me a minute.", "Give me a minute."),
    ("I'll record the call.", "I'll record the call.", "I'll rekord the call."),
    ("That's a record.", "That's a record.", "That's a record."),
    ("I'll present the options.", "I'll present the options.", "I'll prezent the options."),
    ("The site content is updated.", "The site content is updated.", "The site kontent is updated."),
    ("He seems content with it.", "He seems content with it.", "He seems content with it."),
    ("They might object to that.", "They might object to that.", "They might objekt to that."),
    ("Turn the bass down.", "Turn the bass down.", "Turn the base down."),
    ("Let's wind down for the night.", "Let's wind down for the night.", "Let's wined down for the night."),
    ("The wind is picking up.", "The wind is picking up.", "The wind is picking up."),
    ("That's a good use case.", "That's a good use case.", "That's a good yoose case."),
    ("You can use it now.", "You can use it now.", "You can use it now."),
    ("The token is invalid.", "The token is invalid.", "The token is invalid."),
    ("The URL is in the UI.", "The U R L is in the U I.", "The URL is in the UI."),
    ("Run the SQL in Supabase.", "Run the sequel in Supabase.", "Run the sequel in Supabase."),
    ("The npm API is down.", "The npm API is down.", "The npm API is down."),
    ("It peaks at minus six dBFS.", "It peaks at minus six D B F S.", "It peaks at minus six dBFS."),
    ("The PR is open on GitHub.", "The P R is open on GitHub.", "The PR is open on GitHub."),
    ("It's deployed on Vercel.", "It's deployed on Vercel.", "It's deployed on Vercel."),
]


def check() -> int:
    bad = 0
    for text, want_el, want_ko in CASES:
        for engine, want in (("eleven", want_el), ("kokoro", want_ko)):
            got = respell(text, engine)
            if got != want:
                bad += 1
                print(f"FAIL {engine:6} {text!r}\n     got  {got!r}\n     want {want!r}")
    # A clause cut from a sentence is read with the rest of the sentence around it.
    s = "It went out this morning, and the site is live now."
    clause = "and the site is live now."
    if respell(clause, "eleven", s, s.index(clause)) != "and the site is lyve now.":
        bad += 1
        print("FAIL clause in context")
    print(f"{len(CASES) * 2 + 1 - bad} passed, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        sys.exit(check())
    if sys.argv[1:2] == ["say"]:
        for t in sys.argv[2:]:
            for engine in ("eleven", "kokoro"):
                print(f"{engine:6} {respell(t, engine)}")
        sys.exit(0)
    print(__doc__)
