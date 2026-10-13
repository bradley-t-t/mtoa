#!/usr/bin/env python3
"""Canvases: the detail of an answer, put on his screen instead of said.

Sunday says the short answer aloud and, when the answer has structure (steps, a comparison,
numbers over time, several items, code, a breakdown), shows the rest on a full-screen canvas
on whichever of his screens he is at. Nothing in a canvas is ever spoken: it is cut out of the
model's answer here, before the speech gate, the repeat guard or a voice ever see the text.

The model writes the visual part after its spoken sentences, as markdown inside a tag:

    The short answer, one or two sentences. The breakdown is on your screen.

    <canvas title="TCP and UDP">
    ## How they differ
    | | TCP | UDP |
    |---|---|---|
    | Delivery | guaranteed, in order | best effort |

    ```chart
    {"kind": "bar", "title": "Typical latency", "unit": "ms",
     "labels": ["TCP", "UDP"], "series": [{"name": "Latency", "data": [42, 18]}]}
    ```
    </canvas>

Fenced blocks inside a canvas: ```chart (JSON), ```stats (JSON), ```note / ```tip /
```warning (markdown), and any other language is code. Everything else is markdown.

What the popups receive, on /events, and what /show takes as JSON (`spec=`):

    {"type": "canvas", "id": "<qid>", "title": str, "subtitle": str, "question": str,
     "said": str, "source": "reply" | "show", "blocks": [block, ...]}

    {"type": "heading", "text": str, "level": 2 | 3}
    {"type": "text", "md": str}                    markdown: paragraphs, lists, tables, quotes
    {"type": "list", "items": [str, ...], "ordered": bool}
    {"type": "table", "columns": [str, ...], "rows": [[str | number, ...], ...], "align": [...]}
    {"type": "code", "lang": str, "text": str}
    {"type": "chart", "kind": "bar" | "line" | "pie" | "donut", "title": str, "unit": str,
     "labels": [str, ...], "series": [{"name": str, "data": [number | null, ...]}, ...],
     "stacked": bool, "horizontal": bool}
    {"type": "stats", "items": [{"label": str, "value": str, "note": str}, ...]}
    {"type": "callout", "tone": "info" | "good" | "warn" | "bad", "md": str}
    {"type": "divider"}

Everything is checked and capped here, and again in the popup, which renders markdown with raw
HTML turned off and sanitized, and draws charts from the numbers alone. No HTML or script from
the model ever runs.

    canvas.py selftest                      the cases below, on the Mac or the Pi
    canvas.py show <file.json | file.md | -> [--title T]   put a canvas on his screen (/show)
    canvas.py close                         take it down
"""

import json
import math
import re
import sys

MAX_TITLE = 120
MAX_SUBTITLE = 240
MAX_BLOCKS = 40
MAX_TEXT = 6000
MAX_CODE = 12000
MAX_TOTAL = 80000
MAX_ITEMS = 40
MAX_ITEM = 600
MAX_COLS = 8
MAX_ROWS = 60
MAX_CELL = 300
MAX_LABELS = 60
MAX_SERIES = 8          # the palette's eight hues; a ninth series is never a made-up colour
MAX_SLICES = 8          # a pie past this folds the smallest into "Other"
MAX_STATS = 6

KINDS = {"bar": "bar", "column": "bar", "bars": "bar", "line": "line", "area": "line", "lines": "line",
         "pie": "pie", "donut": "donut", "doughnut": "donut"}
TONES = {"info": "info", "note": "info", "good": "good", "tip": "good", "success": "good",
         "warn": "warn", "warning": "warn", "caution": "warn", "bad": "bad", "danger": "bad", "error": "bad"}
LANG = re.compile(r"^[a-z0-9][a-z0-9+#._-]{0,23}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ‪-‮⁦-⁩]")

# ---------------------------------------------------------------- the model's answer

_TAG = re.compile(r"<canvas\b([^>]*)>(.*?)(?:</canvas\s*>|\Z)", re.S | re.I)
_FENCE_JSON = re.compile(r"```canvas[ \t]*\n(\{.*?\})\s*\n```", re.S | re.I)
_ATTR = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def split(text: str) -> tuple:
    """(what is said, the canvas or None, problems). The canvas comes out whole, wherever it
    sits in the answer and whether or not its closing tag made it; what is said is the rest."""
    problems: list = []
    payload = None
    text = text or ""
    parts = []

    def take_tag(m):
        attrs = {k.lower(): (a if a is not None else b) for k, a, b in _ATTR.findall(m.group(1))}
        parts.append(from_markdown(m.group(2), attrs.get("title"), attrs.get("subtitle")))
        return "\n"

    def take_json(m):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError as e:
            problems.append(f"canvas JSON did not parse: {e}")
        return "\n"

    spoken = _FENCE_JSON.sub(take_json, text)
    spoken = _TAG.sub(take_tag, spoken)
    # A tag the model opened and never closed, cut off mid-word, is still not speech.
    spoken = re.sub(r"<canvas\b[^>]*$", "", spoken, flags=re.I)
    spoken = re.sub(r"</canvas\s*>", " ", spoken, flags=re.I)
    if parts:
        merged = {"title": "", "subtitle": "", "blocks": []}
        for p in parts:
            if not isinstance(p, dict):
                continue
            merged["title"] = merged["title"] or p.get("title") or ""
            merged["subtitle"] = merged["subtitle"] or p.get("subtitle") or ""
            merged["blocks"] += p.get("blocks") or []
        payload, more = sanitize(merged)
        problems += more
    spoken = re.sub(r"[ \t]+\n", "\n", spoken)
    spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
    return spoken, payload, problems


_FENCE_OPEN = re.compile(r"^\s{0,3}(```+|~~~+)\s*([\w+#.-]*)[^\n]*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")


def from_markdown(md: str, title: str | None = None, subtitle: str | None = None) -> dict:
    """Markdown, with fenced chart, stats, note and code blocks, as canvas blocks."""
    blocks: list = []
    buf: list = []
    lines = (md or "").replace("\r\n", "\n").split("\n")

    def flush():
        body = "\n".join(buf).strip("\n")
        buf.clear()
        if body.strip():
            blocks.append({"type": "text", "md": body})

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_OPEN.match(line)
        if fence:
            flush()
            mark, lang = fence.group(1), fence.group(2).lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark[:3]):
                body.append(lines[i])
                i += 1
            i += 1    # the closing fence, or the end
            blocks.append(_fenced(lang, "\n".join(body)))
            continue
        head = _HEADING.match(line)
        if head:
            flush()
            level, words = len(head.group(1)), head.group(2).strip()
            if level == 1 and not title and not blocks:
                title = words
            elif words:
                blocks.append({"type": "heading", "text": words, "level": 2 if level <= 2 else 3})
            i += 1
            continue
        buf.append(line)
        i += 1
    flush()
    return {"title": title or "", "subtitle": subtitle or "", "blocks": blocks}


def _fenced(lang: str, body: str) -> dict:
    if lang in ("chart", "graph", "plot"):
        try:
            spec = json.loads(body)
        except ValueError as e:
            return {"type": "_bad", "why": f"chart JSON did not parse: {e}"}
        return {**(spec if isinstance(spec, dict) else {"series": spec}), "type": "chart"}
    if lang in ("stats", "numbers", "kpi", "kpis"):
        try:
            spec = json.loads(body)
        except ValueError as e:
            return {"type": "_bad", "why": f"stats JSON did not parse: {e}"}
        return {"type": "stats", "items": spec.get("items") if isinstance(spec, dict) else spec}
    if lang in TONES or lang == "callout":
        return {"type": "callout", "tone": TONES.get(lang, "info"), "md": body}
    return {"type": "code", "lang": lang, "text": body}


# ---------------------------------------------------------------- checking a canvas

def _clean(s, cap: int, keep_lines: bool = True) -> str:
    if s is None:
        return ""
    if isinstance(s, bool):
        s = "Yes" if s else "No"
    if isinstance(s, (int, float)):
        s = _num_text(s)
    if not isinstance(s, str):
        return ""
    s = _CONTROL.sub("", s.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    "))
    if not keep_lines:
        s = " ".join(s.split())
    return s[:cap].rstrip() if len(s) > cap else s


def _num_text(v) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}" if abs(v) >= 10000 else str(v)
    if isinstance(v, float) and math.isfinite(v):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) >= 10000 else f"{v:.6g}"
    return ""


def _number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v) if isinstance(v, float) else v
    if isinstance(v, str):
        t = v.strip().replace(",", "").rstrip("%")
        try:
            f = float(t)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _chart(b: dict, problems: list) -> dict | None:
    kind = KINDS.get(str(b.get("kind") or b.get("chart") or "bar").lower())
    if not kind:
        problems.append(f"chart kind {b.get('kind')!r} is not bar, line, pie or donut")
        return None
    labels, series = b.get("labels"), b.get("series")
    data = b.get("data")
    if isinstance(data, dict) and not labels:          # {"data": {"A": 3, "B": 5}}
        labels, series = list(data.keys()), [{"name": b.get("name") or "", "data": list(data.values())}]
    elif isinstance(data, list) and not series:        # {"labels": [...], "data": [...]}
        series = [{"name": b.get("name") or "", "data": data}]
    elif isinstance(b.get("values"), list) and not series:
        series = [{"name": b.get("name") or "", "data": b["values"]}]
    if isinstance(series, dict):                       # {"series": {"2025": [...], "2026": [...]}}
        series = [{"name": k, "data": v} for k, v in series.items()]
    if not isinstance(labels, list) or not isinstance(series, list) or not labels or not series:
        problems.append("chart needs labels and series")
        return None
    labels = [_clean(x, 60, False) for x in labels[:MAX_LABELS]]
    out = []
    for s in series[:MAX_SERIES]:
        if isinstance(s, list):
            s = {"name": "", "data": s}
        if not isinstance(s, dict) or not isinstance(s.get("data"), list):
            continue
        vals = [_number(v) for v in s["data"][:len(labels)]]
        vals += [None] * (len(labels) - len(vals))
        if any(v is not None for v in vals):
            out.append({"name": _clean(s.get("name"), 60, False), "data": vals})
    if len(series) > MAX_SERIES:
        problems.append(f"chart had {len(series)} series; kept the first {MAX_SERIES}")
    if not out:
        problems.append("chart has no numbers")
        return None
    chart = {"type": "chart", "kind": kind, "title": _clean(b.get("title"), MAX_TITLE, False),
             "unit": _clean(b.get("unit"), 16, False), "labels": labels, "series": out,
             "stacked": bool(b.get("stacked")) and kind == "bar",
             "horizontal": bool(b.get("horizontal")) and kind == "bar"}
    if kind in ("pie", "donut"):
        vals = [max(0.0, float(v or 0)) for v in out[0]["data"]]
        if not sum(vals):
            problems.append("pie has nothing above zero")
            return None
        pairs = sorted(zip(labels, vals), key=lambda p: -p[1])
        if len(pairs) > MAX_SLICES:
            rest = sum(v for _, v in pairs[MAX_SLICES - 1:])
            pairs = pairs[:MAX_SLICES - 1] + [("Other", rest)]
        chart["labels"] = [p[0] for p in pairs]
        chart["series"] = [{"name": out[0]["name"], "data": [round(p[1], 6) for p in pairs]}]
    return chart


def _block(b, problems: list) -> dict | None:
    if not isinstance(b, dict):
        return None
    kind = str(b.get("type") or "").lower()
    if kind == "_bad":
        problems.append(b.get("why", "a block did not parse"))
        return None
    if kind == "heading":
        text = _clean(b.get("text"), 160, False)
        level = b.get("level") if b.get("level") in (2, 3) else 2
        return {"type": "heading", "text": text, "level": level} if text else None
    if kind in ("text", "markdown", "md", "paragraph"):
        md = _clean(b.get("md") if b.get("md") is not None else b.get("text"), MAX_TEXT)
        return {"type": "text", "md": md} if md.strip() else None
    if kind == "list":
        items = [_clean(x, MAX_ITEM, False) for x in (b.get("items") or [])[:MAX_ITEMS] if x is not None]
        items = [x for x in items if x]
        return {"type": "list", "items": items, "ordered": bool(b.get("ordered"))} if items else None
    if kind == "table":
        cols = [_clean(c, 80, False) for c in (b.get("columns") or b.get("header") or [])[:MAX_COLS]]
        rows = []
        for r in (b.get("rows") or [])[:MAX_ROWS]:
            if isinstance(r, dict) and cols:
                r = [r.get(c, "") for c in cols]
            if not isinstance(r, list):
                continue
            cells = [_clean(c, MAX_CELL, False) for c in r[:MAX_COLS]]
            width = len(cols) or len(cells)
            rows.append((cells + [""] * width)[:width])
        if not rows:
            return None
        width = len(cols) or max(len(r) for r in rows)
        align = [a if a in ("left", "right", "center") else "" for a in (b.get("align") or [])[:width]]
        return {"type": "table", "columns": cols, "rows": rows, "align": align}
    if kind == "code":
        text = _clean(b.get("text") if b.get("text") is not None else b.get("code"), MAX_CODE)
        lang = str(b.get("lang") or b.get("language") or "").lower()
        return {"type": "code", "lang": lang if LANG.match(lang) else "", "text": text} if text.strip() else None
    if kind == "chart":
        return _chart(b, problems)
    if kind == "stats":
        items = []
        for it in (b.get("items") or [])[:MAX_STATS]:
            if isinstance(it, dict) and it.get("value") is not None:
                items.append({"label": _clean(it.get("label"), 60, False), "value": _clean(it.get("value"), 24, False),
                              "note": _clean(it.get("note"), 80, False)})
        return {"type": "stats", "items": items} if items else None
    if kind == "callout":
        md = _clean(b.get("md") if b.get("md") is not None else b.get("text"), 1200)
        return {"type": "callout", "tone": TONES.get(str(b.get("tone") or "info").lower(), "info"), "md": md} \
            if md.strip() else None
    if kind == "divider":
        return {"type": "divider"}
    problems.append(f"unknown block type {kind!r}")
    return None


def sanitize(payload) -> tuple:
    """(a canvas fit to send, problems), or (None, problems) when nothing in it can be shown."""
    problems: list = []
    if not isinstance(payload, dict):
        return None, ["a canvas is a JSON object with a title and blocks"]
    blocks, size = [], 0
    for b in (payload.get("blocks") or [])[:MAX_BLOCKS * 2]:
        clean = _block(b, problems)
        if not clean:
            continue
        size += len(json.dumps(clean))
        if size > MAX_TOTAL or len(blocks) >= MAX_BLOCKS:
            problems.append("the canvas was too long; the end was left off")
            break
        if clean["type"] == "divider" and (not blocks or blocks[-1]["type"] == "divider"):
            continue
        blocks.append(clean)
    while blocks and blocks[-1]["type"] == "divider":
        blocks.pop()
    if not blocks:
        return None, problems + ["nothing in the canvas could be shown"]
    title = _clean(payload.get("title"), MAX_TITLE, False)
    if not title:
        first = blocks[0]
        if first["type"] == "heading":
            title = first["text"]
            blocks = blocks[1:] or blocks
    return {"title": title, "subtitle": _clean(payload.get("subtitle"), MAX_SUBTITLE, False), "blocks": blocks}, problems


_LIST_LINE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+\S", re.M)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)


def worth_showing(payload: dict | None) -> tuple:
    """(show it, why). The code half of the rule for when a canvas is used; the prompt is the
    other half. A canvas needs structure a voice cannot carry: a table, code, a chart, figures,
    a list of three or more, two or more sections, or more text than two spoken sentences."""
    if not payload:
        return False, "no canvas"
    blocks = payload["blocks"]
    kinds = [b["type"] for b in blocks]
    for k in ("table", "code", "chart", "stats"):
        if k in kinds:
            return True, k
    if any(b["type"] == "list" and len(b["items"]) >= 3 for b in blocks):
        return True, "a list"
    text = "\n".join(b.get("md", "") for b in blocks if b["type"] in ("text", "callout"))
    if _TABLE_RULE.search(text):
        return True, "a table"
    if len(_LIST_LINE.findall(text)) >= 3:
        return True, "a list"
    if kinds.count("heading") >= 2:
        return True, "sections"
    if len(text) + sum(len(" ".join(b.get("items", []))) for b in blocks) >= 350:
        return True, "long text"
    return False, "too little to need a screen"


def plain_text(payload: dict) -> str:
    """The canvas as plain sentences, for when a canvas too small to show was all the answer had."""
    out = []
    for b in payload.get("blocks", []):
        if b["type"] in ("text", "callout"):
            out.append(re.sub(r"[*_`#>|]+", "", b["md"]))
        elif b["type"] == "list":
            out += b["items"]
    return " ".join(" ".join(out).split())


def trim_spoken(spoken: str, words: int = 45) -> str:
    """With the detail on his screen, what is said stays short: two sentences at most, and no
    more than about forty-five words, cut at a sentence."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", " ".join((spoken or "").split())) if s]
    kept: list = []
    for s in sents[:2]:
        if kept and len(" ".join(kept + [s]).split()) > words:
            break
        kept.append(s)
    return " ".join(kept)


_ABOUT_SCREEN = re.compile(r"\bon (?:your|the|his) screens?\b", re.I)


def said_line(spoken: str) -> str:
    """What was said, as the canvas shows it under its title: a sentence that only points at the
    screen ("The steps are on your screen.") is left out, since he is already looking at it."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", " ".join((spoken or "").split())) if s]
    kept = [s for s in sents if not (_ABOUT_SCREEN.search(s) and len(s.split()) <= 14)]
    return " ".join(kept)


# ---------------------------------------------------------------- closing it by voice

# Said while a canvas is up, these close it without a model turn, the way "stop" and "mute"
# act. Only then: with nothing on his screen, "close that" or "thanks" go to Sunday as before.
_LEAD = r"^(?:(?:hey|okay|ok|alright|all right|oh|um|uh)[,.!]?\s+)?(?:sunday[,.!]?\s+)?(?:(?:okay|ok|alright|all right|great|perfect|cool|nice)[,.!]?\s+)?(?:(?:please|you can|go ahead and|can you|could you)\s+)?"
_TARGET = r"(?:that|this|it|the (?:screen|canvas|details?|chart|charts|graph|table|list|card|breakdown|steps|code|page|panel))"
_CLOSE = (r"(?:(?:close|hide|dismiss|clear|remove|take down|put away|get rid of|lose|drop)(?: " + _TARGET + r")?"
          r"|(?:take|put) " + _TARGET + r" (?:down|away))(?: (?:down|away|now))?")
_DONE = (r"(?:got it|thanks|thank you|thanks a lot|thanks so much|thank you so much|thank you very much|cheers|"
         r"(?:i'?m |i am |we'?re )?done|i'?ve got it|i have it|that'?s all|that'?s it|all good|makes sense|understood|"
         r"(?:got it|okay|ok|perfect|great|cool|nice|awesome)[,.!]?\s+(?:thanks|thank you))")
_TAIL = r"(?:[,.!]?\s+(?:sunday|thanks|thank you))?(?:[,.!]?\s+please)?[.!]*$"
DISMISS_SAID = re.compile(_LEAD + r"(?:" + _CLOSE + r"|" + _DONE + r")" + _TAIL, re.I)


def is_dismissal(text: str) -> bool:
    plain = " ".join((text or "").replace("’", "'").split()).strip(" ,")
    return bool(plain) and bool(DISMISS_SAID.match(plain))


# ---------------------------------------------------------------- tests

DISMISS_CASES = [
    ("Close that.", True), ("Sunday, close that.", True), ("Hide that", True), ("Got it.", True),
    ("Thanks.", True), ("Thank you.", True), ("Thanks, Sunday.", True), ("Okay, thanks.", True),
    ("Got it, thanks.", True), ("Sunday, hide the chart.", True), ("Close it.", True), ("Dismiss.", True),
    ("Clear the screen.", True), ("That's all.", True), ("I'm done.", True), ("Perfect, thank you.", True),
    ("Hey Sunday, close this please.", True), ("Take that down.", True), ("Put it away.", True),
    ("Take down the chart.", True), ("You can close that.", True), ("All right, close it.", True),
    # Not a dismissal: these go to Sunday as questions or as other commands.
    ("Close that tab in Safari.", False), ("Thanks, what about UDP?", False), ("Close the window.", False),
    ("Hide the overlay.", False), ("Can you close my email?", False), ("What time is it?", False),
    ("Stop.", False), ("Okay.", False), ("Got it working yet?", False), ("Close the garage door.", False),
    ("Thanks for the help with the site, is it live?", False), ("Done with the emails?", False),
]

_EXAMPLE = """TCP makes sure every packet arrives in order, and UDP just sends them. The breakdown is on your screen.

<canvas title="TCP and UDP">
## How they differ
| | TCP | UDP |
|---|---|---|
| Delivery | guaranteed, in order | best effort |
| Speed | slower | faster |

```chart
{"kind": "bar", "title": "Typical latency", "unit": "ms", "labels": ["TCP", "UDP"],
 "series": [{"name": "Latency", "data": [42, 18]}]}
```

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
```
</canvas>"""


def _cases() -> list:
    """(name, passed, detail)"""
    out = []

    def case(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    spoken, c, probs = split(_EXAMPLE)
    case("spoken part is only the sentences", spoken == "TCP makes sure every packet arrives in order, and UDP just "
         "sends them. The breakdown is on your screen.", spoken)
    case("nothing of the canvas is left in what is said", "<canvas" not in spoken and "|" not in spoken
         and "chart" not in spoken and "socket" not in spoken, spoken)
    case("title from the tag", c and c["title"] == "TCP and UDP", c and c["title"])
    kinds = [b["type"] for b in c["blocks"]] if c else []
    case("blocks in order", kinds == ["heading", "text", "chart", "code"], kinds)
    case("chart kept its numbers", c and c["blocks"][2]["series"][0]["data"] == [42, 18], c and c["blocks"][2])
    case("code kept its language", c and c["blocks"][3]["lang"] == "python")
    case("no problems", not probs, probs)
    case("worth showing", worth_showing(c)[0], worth_showing(c))

    s, c, _ = split("It's 7:20.")
    case("a plain answer has no canvas", s == "It's 7:20." and c is None, (s, c))

    s, c, p = split("The steps are on your screen.\n<canvas title=\"Steps\">\n1. One\n2. Two\n3. Three")
    case("an unclosed canvas is still cut out", s == "The steps are on your screen." and c and c["blocks"], (s, c, p))
    s, _, _ = split("Here it is. <canvas title=\"x\"")
    case("a tag cut off mid-way is not spoken", s == "Here it is.", s)

    s, c, p = split('One sentence.\n```canvas\n{"title": "Sizes", "blocks": [{"type": "stats", "items": '
                    '[{"label": "Disk", "value": "82%"}]}]}\n```')
    case("JSON canvas fence", s == "One sentence." and c and c["blocks"][0]["type"] == "stats", (s, c, p))

    s, c, p = split("Two parts.<canvas>\n# Title From Heading\nSome text that is long enough.</canvas>")
    case("title from a first-level heading", c and c["title"] == "Title From Heading", c)

    s, c, p = split("Bad chart.<canvas title=\"x\">\n```chart\n{not json\n```\n</canvas>")
    case("a chart that does not parse is dropped and said so", c is None and any("chart" in x for x in p), (c, p))

    # Sanitizing: what reaches the popup is data, never markup or script to run.
    c, p = sanitize({"title": "<script>alert(1)</script>T", "blocks": [
        {"type": "text", "md": "Hi <img src=x onerror=alert(1)>"},
        {"type": "html", "html": "<b>x</b>"},
        {"type": "chart", "kind": "bar", "labels": ["a", "b"], "series": [{"name": "s", "data": [1, "NaN"]}]},
        {"type": "chart", "kind": "radar", "labels": ["a"], "series": [{"data": [1]}]},
        {"type": "code", "lang": "x\" onload=\"y", "text": "print(1)"},
        {"type": "table", "columns": ["a", "b"], "rows": [[1, 2, 3], ["x"]]},
        {"type": "chart", "kind": "line", "labels": ["a", "b"], "series": [{"data": [float("inf"), 2]}]},
    ]})
    kinds = [b["type"] for b in c["blocks"]]
    case("unknown block types are dropped", "html" not in kinds and any("html" in x for x in p), (kinds, p))
    case("an unknown chart kind is dropped", sum(k == "chart" for k in kinds) == 2, kinds)
    case("NaN and infinity become gaps", c["blocks"][1]["series"][0]["data"] == [1, None]
         and c["blocks"][4]["series"][0]["data"] == [None, 2], c["blocks"][1])
    case("a code language is a plain name or nothing", c["blocks"][2]["lang"] == "", c["blocks"][2])
    case("table rows fit the columns", c["blocks"][3]["rows"] == [["1", "2"], ["x", ""]], c["blocks"][3])
    case("text is kept as text for the popup to escape", c["blocks"][0]["md"].startswith("Hi <img"), c["blocks"][0])

    c, _ = sanitize({"title": "Share", "blocks": [{"type": "chart", "kind": "pie",
                                                   "labels": list("abcdefghij"), "series": [{"data": list(range(10, 0, -1))}]}]})
    ch = c["blocks"][0]
    case("a pie past eight slices folds into Other", len(ch["labels"]) == 8 and ch["labels"][-1] == "Other"
         and ch["series"][0]["data"][-1] == 6, ch)
    c, _ = sanitize({"blocks": [{"type": "chart", "kind": "bar", "labels": ["a"],
                                 "series": [{"name": str(i), "data": [i]} for i in range(12)]}]})
    case("never more than eight series", len(c["blocks"][0]["series"]) == 8)
    c, _ = sanitize({"blocks": [{"type": "chart", "kind": "bar", "data": {"Mon": 3, "Tue": "4"}}]})
    case("chart data as a mapping", c and c["blocks"][0]["labels"] == ["Mon", "Tue"]
         and c["blocks"][0]["series"][0]["data"] == [3, 4.0], c)
    c, p = sanitize({"title": "x", "blocks": [{"type": "text", "md": "a" * 7000}] * 30})
    case("size is capped", c and len(json.dumps(c)) < MAX_TOTAL + 8000 and p, len(json.dumps(c or {})))
    c, _ = sanitize({"title": "x‮evil\x00", "blocks": [{"type": "text", "md": "ok"}]})
    case("control and direction characters are taken out", c["title"] == "xevil", c["title"])
    case("a canvas with nothing in it is refused", sanitize({"title": "x", "blocks": []})[0] is None)
    case("not an object is refused", sanitize(["x"])[0] is None)

    # The rule for when a canvas is used.
    small = sanitize({"title": "Time", "blocks": [{"type": "text", "md": "It is 7:20."}]})[0]
    case("a one-line canvas is not worth a screen", not worth_showing(small)[0])
    lst = sanitize({"title": "Steps", "blocks": [{"type": "text", "md": "1. a\n2. b\n3. c"}]})[0]
    case("a three-step list is", worth_showing(lst)[0])
    case("a table is", worth_showing(sanitize({"blocks": [{"type": "table", "rows": [["a"]]}]})[0])[0])

    case("spoken part trimmed to two sentences", trim_spoken("One. Two. Three. Four.") == "One. Two.")
    long = "word " * 60 + "end. Second."
    case("a long first sentence is kept whole", trim_spoken(long) == " ".join(long.split()[:61]))

    for text, want in DISMISS_CASES:
        case(f"dismissal: {text!r} -> {want}", is_dismissal(text) == want)
    return out


def _said_cases() -> list:
    rows = [
        ("Your computer asks a DNS server for the address. The steps are on your screen.",
         "Your computer asks a DNS server for the address."),
        ("I've put the breakdown by folder on your screen.", ""),
        ("Disk use is at forty percent, and the numbers on your screen are from just now, measured on the Pi itself "
         "over the last hour of work.",
         "Disk use is at forty percent, and the numbers on your screen are from just now, measured on the Pi itself "
         "over the last hour of work."),
        ("TCP checks every packet. UDP does not.", "TCP checks every packet. UDP does not."),
    ]
    return [(f"said line: {a[:50]!r}", said_line(a) == want, said_line(a)) for a, want in rows]


def _gate_cases() -> list:
    """The speech gate's code half on canvas lines: saying the detail is on his screen is cleared
    only when a canvas went with the answer; asking him to do something on a screen never is."""
    try:
        import speech_gate
    except Exception as e:      # run from somewhere without it
        return [("speech_gate importable", False, str(e))]
    rows = [
        ("The breakdown is on your screen.", "ABCD", True, []),
        ("I've put the steps on your screen.", "ABCD", True, []),
        ("The breakdown is on your screen.", "ABCD", False, ["B"]),
        ("It's on your screen, so click Approve.", "ABCD", True, ["B"]),
        ("The chart is on your screen; type yes when you're ready.", "ABCD", True, ["B"]),
        ("Postgres is faster for writes. The numbers are up on your screen now.", "ABCD", True, []),
        ("Disk use is at eighty two percent. I've put the breakdown by folder on your screen.", "ABCD", True, []),
        ("The full list of tickets is on your screen.", "ABCD", True, []),
        ("I've put the steps to click on your screen.", "ABCD", True, ["B"]),
        ("The link you should open is on your screen.", "ABCD", True, ["B"]),
    ]
    out = []
    for text, rules, shown, want in rows:
        got = speech_gate.prefilter(text, [], rules, shown="A canvas" if shown else "")
        out.append((f"gate prefilter ({'shown' if shown else 'nothing shown'}): {text!r} -> {want}", got == want, got))
    return out


def selftest() -> int:
    bad = 0
    for name, ok, detail in _cases() + _gate_cases() + _said_cases():
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail!r}"[:400]))
    print(f"{'all passed' if not bad else f'{bad} failed'}")
    return bad


# ---------------------------------------------------------------- putting one on his screen

def _send(query: str, url: str) -> dict:
    from urllib.request import urlopen
    with urlopen(f"{url}/show?{query}", timeout=15) as r:
        return json.loads(r.read().decode())


def main(argv: list) -> int:
    from urllib.parse import urlencode
    url = "http://127.0.0.1:8794"
    if "--url" in argv:
        url = argv[argv.index("--url") + 1]
    if argv[:1] == ["selftest"]:
        return 1 if selftest() else 0
    if argv[:1] == ["close"]:
        print(json.dumps(_send("close=1", url)))
        return 0
    if argv[:1] == ["show"] and len(argv) > 1:
        src = argv[1]
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        title = argv[argv.index("--title") + 1] if "--title" in argv else ""
        if raw.lstrip().startswith("{"):
            spec = json.loads(raw)
            if title:
                spec["title"] = title
            query = urlencode({"spec": json.dumps(spec, separators=(",", ":"))})
        else:
            query = urlencode({"md": raw, "title": title})
        res = _send(query, url)
        print(json.dumps(res))
        return 0 if res.get("ok") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
