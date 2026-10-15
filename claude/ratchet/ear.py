"""Ear: a Sunday announcement has to be written the way it is said.

The voice reads every character it is given, once, at speaking speed, to someone
who cannot look back at it. "Oct 2" comes out as "oct two", "CDT" as three
letters, "$792.30" digit by digit, and a chat reply's "Why:" and "What:" as
words. A line written for a screen is therefore unintelligible aloud, however
true it is. This check reads the text of a /say before it is sent and refuses
it when it is written for the eye, naming each piece and how a person would say
it. It needs no judge: every rule here is a pattern, so it holds the same text
the same way every time and costs nothing.

It runs from ratchet.pre_say, ahead of the speech judge. The voice server
rewrites what slips past it (sunday-voice server/spoken.py); this is what keeps
the writing itself human, which no rewrite can do: a chat reply's layout, an ID,
a file name or a paragraph of figures has no spoken form to be turned into.

    python3 ~/.claude/ratchet/ear.py check "<text>"
    python3 ~/.claude/ratchet/ear.py selftest
"""
import json
import re
import sys
from pathlib import Path

_MON = r"(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)"
_MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"

# Each row: a name, the pattern, and how a person says it instead.
RULES = [
    ("layout", re.compile(r"(?:^|(?<=[.!?]\s)|(?<=\n))\s*(?:Why|What|Touched|Proof|Note|Summary|Status|TL;?DR)\s*:", re.I),
     "that is a written reply's layout; say the answer as plain sentences, with no labels"),
    ("list", re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S"),
     "no lists out loud; say how many and the one that matters"),
    ("abbreviated date", re.compile(rf"\b{_MON}\.?\s*\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s*\d{{4}})?|\b{_MON}\.(?=\s)"
                                    r"|\b(?:Mon|Tue|Tues|Wed|Thu|Thurs?|Fri|Sat)\b\.?,?(?=\s+(?:" + _MON + "|" + _MONTHS + r"|\d))"),
     'the month and day in full words, the year only if it is not this year: "October second"'),
    ("numeric date", re.compile(r"\b(?:19|20)\d\d-\d\d-\d\d\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
     'say the date: "October fifteenth"'),
    ("ordinal figure", re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s*\d{{4}})?"),
     'the day as a word, and no year if it is this year: "October fifteenth"'),
    ("time zone", re.compile(r"\b(?:[CEPM][SD]T|AK[SD]T|HST|UTC|GMT)\b"),
     'leave the time zone out; if it matters, say "Central time"'),
    ("clock figure", re.compile(r"\b\d{1,2}:\d\d(?::\d\d)?\b|\b\d{1,2}\s?(?:[ap]\.?m\.?|[AP]M)(?![A-Za-z])"),
     'the time in words, the way you would say it: "ten thirty", "just after nine tonight", "midnight"'),
    ("money figure", re.compile(r"[-−]?\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB]n?\b)?"),
     'the amount in words, rounded the way a person would: "about two hundred forty dollars"'),
    ("version", re.compile(r"\bv?\d+(?:\.\d+){2,}\b"),
     'a version number is noise aloud; say "the new version" or what changed'),
    ("big figure", re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d{2,}\b|\b\d{5,}\b"),
     'round it and say it in words: "about twelve hundred", "just over a thousand"'),
    ("symbol", re.compile(r"(?<=\s)[=+−×&~≈#@|]|[=+−×≈](?=\s)|→|->|=>|(?<=\d)[x×](?=\s)|~(?=\d)|#(?=\d)"),
     "say the word, or say it another way: plus, minus, equals, and, about"),
    ("brackets", re.compile(r"[()\[\]{}<>]"),
     "an aside in brackets cannot be heard; make it its own short sentence or leave it out"),
    ("shorthand", re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.?|vs\.?|approx\.?|incl\.|est\.|w/o?|b/c|N/A|n/a|TBD|TBA|FYI|IIRC|AFAIK|IMO)(?![A-Za-z])"),
     "say the words: for example, that is, and so on, versus, about, with"),
    ("unit", re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:ms|secs?|mins?|hrs?|GB|MB|KB|TB|GHz|MHz|kbps|Mbps|Gbps|fps|px|k|K)\b"),
     'the unit in full: "two hundred milliseconds", "five minutes"'),
    ("code", re.compile(r"https?://\S+|\bwww\.\S+|[\w.+-]+@[\w-]+\.\w[\w.]*|(?:~|\.{1,2})?/[\w.-]+/[\w./-]+"
                        r"|\b[\w-]+\.(?:py|js|ts|tsx|jsx|json|md|sh|html|css|ya?ml|toml|sql|txt|log|plist|swift|env)\b"
                        r"|`[^`]+`|\b[a-z]+(?:_[a-z0-9]+)+\b|\bv?\d+\.\d+\.\d+\b"),
     "a link, path, file name or code cannot be said; name the thing instead: \"the status page\", \"the voice server\""),
    ("ID", re.compile(r"\b(?=[A-Za-z0-9_]*\d)(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]{9,}\b|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"),
     'an ID or hash is noise aloud; leave it out or name what it belongs to: "the client\'s account"'),
]

# Past this, it is a written report, not something said.
MOST_WORDS = 90
MOST_SENTENCES = 6

# A yes or no is the word and a few words at most, as sunday-voice's spoken.brief cuts it.
YES_NO = re.compile(r"\s*(?:yes|no|yeah|yep|nope|not yet)(?=[.,;:!\u2014\u2013-]|\s*$)", re.I)
BRIEF_WORDS = 10

HOW = """{assistant} says this out loud, once, at speaking speed, and the voice reads every character it is given: "Oct 2" comes out as "oct two", "CDT" as three letters and "$792.30" digit by digit. Rewrite it the way you would say it to {him} across the room, then send it again:
- Dates, times and amounts in words, the way people say them: "October second", "just after ten thirty", "about two hundred forty dollars". No year when it is this year, and no time zone.
- No abbreviations, symbols, brackets, IDs, file names, links, labels or lists. Name the thing instead.
- Sound like a person talking: contractions, short sentences, a comma where you would take a breath, and the word that matters last, where the voice lands on it.
- Two or three sentences: the verdict, and the one detail that matters. A yes or no is the word and a few words at most."""

HOLD = """Ratchet: this announcement is written to be read, not heard, so it was not spoken.

%s
%s"""


def _render(text):
    try:
        import premature
        return premature._render(text)
    except Exception:
        return text


def findings(text):
    """[(rule, fragment, how to say it)] for each piece of the text written for the eye."""
    found, taken = [], []
    text = text or ""
    for name, rx, how in RULES:
        for m in rx.finditer(text):
            s, e = m.span()
            frag = m.group(0).strip()
            if not frag or any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            found.append((name, frag, how))
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    sentences = len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])
    if words > MOST_WORDS or sentences > MOST_SENTENCES:
        found.append(("length", "%d words in %d sentences" % (words, sentences),
                      "too long to follow by ear; say the verdict and the one detail that matters in two or three sentences"))
    if YES_NO.match(text) and words > BRIEF_WORDS:
        found.append(("yes or no", "%d words" % words,
                      'a yes or no is the word and a few words at most: "Yes, every two hours." Leave the rest unsaid; '
                      "he asks if he wants it"))
    return found


def reason(found, short=False):
    """What the turn is told: each piece, grouped by rule, and how to say it."""
    lines, seen = [], {}
    for name, frag, how in found:
        seen.setdefault((name, how), []).append(frag)
    for (name, how), frags in seen.items():
        shown = ", ".join('"%s"' % (f if len(f) <= 60 else f[:57] + "...") for f in frags[:4])
        more = " and %d more" % (len(frags) - 4) if len(frags) > 4 else ""
        lines.append("  %s%s  -> %s" % (shown, more, how))
    quoted = "\n".join(lines) + "\n"
    return HOLD % (quoted, "" if short else _render(HOW))


# What must be held, and what must pass, with this year being any year.
HELD = [
    "Yes, the overlay is running on both machines and the small bar now matches the caption design.",
    "No. The deploy stopped before the restart, so the old server kept running.",
    "Re-checked. The $792.30 is correct as gross charges, but $543.32 of it was passed straight back out.",
    "The window is Oct 1 2026 00:00 CDT to Oct 15 10:52 CDT.",
    "It's due Oct 2.",
    "Why: the earlier total counted every charge.",
    "Renewal is on 2026-10-27.",
    "It renews on 11/3.",
    "Sending stops at 5 p.m. CST.",
    "The deploy (the second one) failed.",
    "Copied 1,204 files.",
    "It answered in 250 ms.",
    "The numbers add up: 792.30 − 24.77 = 767.53.",
    "Those went to acct_1AbcDEfgHIjkLMno.",
    "The fix is in voice_server.py now.",
    "It's live at https://example.com/status.",
    "Fix the copy, e.g. the hero line.",
    "Commit cb8cc6c is on main.",
    "It's 10:34 PM.",
    "Thu, Oct 15 is the day.",
    "It's on October 15th, 2026.",
    "- the menu\n- the footer",
    "Mac & PC both got it.",
    "The fix is live as version 2026.41.17.",
    ("The site is live and the letters went out this morning. Every one of the thirteen previews loaded on a phone, "
     "the forms reached your inbox, and the payment link opened checkout. I also checked the old links, which still "
     "redirect to the new pages, and the sitemap now lists every page. Google has been asked to crawl it again, and "
     "the speed scores held above ninety on both phone and desktop. Tomorrow morning the next batch of previews "
     "builds at seven, the letters for them go out at eight, and I will tell you how many people opened them by "
     "the end of the day."),
]
CLEAR = [
    "Yes, every two hours.",
    "No, Tuesday's release carried it.",
    "No one has replied yet, so the second letters go out on Monday morning.",
    "Done. Both resumes are in Documents now.",
    "It's ten thirty-four at night.",
    "Your payout of about a hundred and fifty dollars came in on Thursday.",
    "The site is live, and the letters go out at seven tomorrow morning.",
    "On your emails, nothing new from a person since yesterday.",
    "I'm rebuilding the overlay now, and I'll tell you when it's on your Mac.",
    "You kept about two hundred forty-four dollars in October.",
    "Yes, all thirteen previews loaded on a phone.",
    "The breakdown is on your screen.",
    "It's three times faster than it was this morning.",
    "I moved the menu fix to GitHub and it's live on Vercel.",
    "Seven of the eight sites passed, and the last one is still loading.",
]


def selftest():
    bad = 0
    for text in HELD:
        if not findings(text):
            bad += 1
            print("MISSED: %s" % " ".join(text.split())[:88])
    for text in CLEAR:
        f = findings(text)
        if f:
            bad += 1
            print("FALSE:  %s  <- %s %r" % (" ".join(text.split())[:66], f[0][0], f[0][1]))
    print("ear selftest: %d held, %d clear, %d wrong" % (len(HELD), len(CLEAR), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        f = findings(" ".join(sys.argv[2:]))
        print(reason(f) if f else "clear: written the way it is said")
        sys.exit(0)
    print(__doc__)
