#!/usr/bin/env python3
"""Scores the repo's prose for AI writing tells, and blocks landing text that has them.

Deliberately a simpler mechanism than a ledger-backed pass. A comment pass
is a judgement no tool can make, so that one records that a human-or-model pass
happened. AI-writing tells are detectable, so this one detects them: the gate is
the current text, re-read on every check, and fixing the prose is what opens it.
There is no "I did the pass" ledger to mark, because there is nothing to take on
faith.

What it reads is every text file the repo tracks — documentation, changelogs, and
the config files that hold user-facing copy — plus the commit message and PR body
of the command being run, which are the two pieces of writing that never live in a
file long enough for a later sweep to catch them.

Detection is the vendored avoid-ai-writing engine (`tools/ai-writing/`), whose own
documentation is blunt about what its output means: signals, not proof. So only
the lexical categories block — a named phrase somebody can go and change. The
whole-document rhythm and punctuation measures are reported and never gate, since
"72 em dashes in 5763 words" describes an author's voice rather than a defect, and
a gate that argues with someone's voice gets switched off.

A flagged phrase that is right anyway — a literal `beacon`, a `## Features`
heading, an idiom the author actually uses — is retired with `allow`, which takes
a reason and records it against that file and that phrase. Not against the file's
contents: a whole-file exemption would quietly forgive every tell added to it
afterwards.

    status  what the current repo scores, and what is blocking
    list    paths with unresolved blocking issues, one per line
    check   score the given paths (or --stdin), exit 1 if any block
    allow   retire one flagged phrase in one file, with a reason
    allows  what has been retired, and why
    guard   PreToolUse(Bash) hook: refuse `git commit` / `gh pr create` while
            any tracked text or the message being written is flagged

The landing gate above is scoped to repos under the owner's accounts, matching
a comment pass. A cloned third-party repo is not part of this workflow and is
left alone.

The copywriting skill arms a second gate, which is not, because the copy it
writes is as often a reply or a page in somebody else's tree as it is a tracked
file:

    guard-skill  PreToolUse(Skill) hook: arm the gate for the copywriting skill
    hook-edit    PostToolUse(Write|Edit) hook: record a written path while armed
    guard-stop   Stop hook: refuse the ending while any recorded file, or the
                 reply carrying the copy, is flagged
"""

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _shared():
    """The shared _lib module, loaded by path.

    Hooks run this tool as a script from arbitrary directories, so the module
    is found beside this file rather than through sys.path, with the installed
    tree as the fallback for a copy executed from elsewhere.
    """
    here = Path(__file__).resolve().parent / "_lib.py"
    path = here if here.is_file() else Path.home() / ".claude" / "tools" / "_lib.py"
    spec = importlib.util.spec_from_file_location("intelligence_tools_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The guard is fail-closed, so a missing or unreadable _lib.py must not crash
# the hook into silence: main() turns it into the same block an unrunnable
# detector gets, and every other verb reports it and exits 1.
try:
    _lib = _shared()
    _LIB_ERROR = None
except Exception as err:
    _lib = None
    _LIB_ERROR = err

if _lib is not None:
    _state_root = _lib.state_root
    run = _lib.run
    repo_root = _lib.repo_root
    origin_slug = _lib.origin_slug
    is_owned = _lib.is_owned
    vendored_roots = _lib.vendored_roots
    is_vendored = _lib.is_vendored
    read_transcript = _lib.read_transcript
    final_reply = _lib.final_reply
    STATE_DIR = _state_root() / "writing-pass"
else:
    STATE_DIR = None

# What a repo with no allow ledger on record starts from.
EMPTY_STATE = {"allows": {}}


def _scanner():
    """The vendored detector, preferring the copy beside this file.

    The sibling copy is the one that travels with the tool — into a bare CI
    checkout, or wherever INTELLIGENCE_TEST_TARGET points a case run — and the
    installed tree is the fallback for a copy executed from somewhere else.
    """
    beside = Path(__file__).resolve().parent / "ai-writing" / "scan.js"
    if beside.is_file():
        return beside
    return Path.home() / ".claude" / "tools" / "ai-writing" / "scan.js"


SCANNER = _scanner()

# Categories naming a specific phrase in the text. Each one points at something
# that can be rewritten, which is what makes it fair to gate on.
BLOCKING_TYPES = {
    "acknowledgment-loop", "ai-citation-markup", "ai-placeholder", "ai-utm-source",
    "chatbot", "confidence-calibration", "cutoff-disclaimer", "false-concession",
    "filler", "formulaic-opener", "future-narrative", "generic-conclusion",
    "hashtag-stuff", "hedge-stack", "hollow-intensifier", "lets-construction",
    "lingering-attention", "normalization-flag", "novelty-inflation",
    "parenthetical-hedge", "real-actual-inflation", "reasoning-artifact",
    "rhetorical-question", "significance-inflation", "social-cta-closer",
    "speculative-opener", "sycophantic", "template-phrase", "tier1",
    "tier1-clarity", "tier2", "tier3", "tier3-phrase", "tier3-phrase-cluster",
    "transition", "vague-attribution",
}

# Prose can read as machine-written without using any of the vocabulary above,
# so the score is a backstop. Measured documents in this workspace sit between 0
# and 7, and the engine calls 35 the top of "some AI patterns" — 40 is clear of
# anything an ordinary document produces and only fires on text that is
# uniformly machine-shaped.
SCORE_CEILING = 40

TEXT_SUFFIXES = {".adoc", ".markdown", ".md", ".mdx", ".rst"}

# Formats that hold user-facing copy as often as documentation does — in-game
# messages, interface strings, translation tables — but only when the path says
# so. Read as prose these are mostly keys and syntax, which the score reflects;
# the lexical categories still find a tell inside a string, and those are the
# only ones that gate.
#
# `.txt` belongs here rather than with the documents above. Its prose-to-noise
# ratio is the worst of any extension in this workspace: `robots.txt`, and a
# Paradox mod whose entire script tree is `.txt`, where a name list scans as a
# vocabulary complaint about invented words.
COPY_CONFIG_SUFFIXES = {".json", ".properties", ".txt", ".yaml", ".yml"}
COPY_CONFIG_RE = re.compile(
    r"(?:^|[/_.-])(content|copy|i18n|l10n|lang|locale|locales|localisation|"
    r"localization|message|messages|string|strings|text)(?:$|[/_.-])",
    re.IGNORECASE,
)

EXCLUDED_DIRS = {
    ".intelligence", ".next", ".nuxt", ".svelte-kit", ".venv", "__pycache__", "build",
    "coverage", "dist", "node_modules", "out", "target", "vendor",
    # A skills/ or checklists/ tree holds definitions and their references -
    # instruction for the model, not product copy a user reads, and often
    # vendored third-party material. Both carry domain vocabulary the detector
    # reads as tells: a design reference is about the words, so it has to name
    # them.
    "skills", "checklists",
}

# Text carried into the repo rather than written in it. Holding a license or a
# generated dependency list to an authorial standard flags text nobody here may
# rewrite. `skill` covers a SKILL.md definition: it is internal instruction, and
# the one that documents these very patterns must quote them as examples, so it
# would flag forever.
EXCLUDED_STEMS = ("license", "copying", "notice", "third-party", "third_party",
                  "code_of_conduct", "code-of-conduct", "skill")

# A ceiling on the detector's work, because every file costs a node process and a
# repo can track thousands. Nothing here has come close. Truncation is announced
# rather than silent: a partial sweep reporting "nothing blocking" is the same
# failure ScannerUnavailable exists to prevent, arriving through a different door.
MAX_SCAN_FILES = 400


def state_path(slug):
    return _lib.state_path(STATE_DIR, slug)


def load_state(slug):
    return _lib.load_state(STATE_DIR, slug, EMPTY_STATE)


def save_state(slug, state):
    _lib.save_state(STATE_DIR, slug, state)


# A VENDOR.md subtree is upstream's copy, kept so it can be replaced wholesale
# when upstream moves. Its prose is not this author's to rewrite, so gating on
# it would be a gate with no way to open it. Same marker the comment and
# documentation ledgers read.
def is_candidate(rel, vendored=frozenset()):
    path = Path(rel)
    if EXCLUDED_DIRS.intersection(path.parts):
        return False
    if is_vendored(path, vendored):
        return False
    if path.stem.lower() in EXCLUDED_STEMS or path.name.lower().startswith(EXCLUDED_STEMS):
        return False
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return True
    return suffix in COPY_CONFIG_SUFFIXES and bool(COPY_CONFIG_RE.search(rel))


def candidates(root):
    out = run(["git", "ls-files", "-z"], cwd=root)
    if not out:
        return []
    paths = [p for p in out.split("\0") if p]
    vendored = vendored_roots(paths)
    tracked = sorted(
        p for p in paths if is_candidate(p, vendored) and (root / p).is_file()
    )
    if len(tracked) > MAX_SCAN_FILES:
        print(f"writing-pass: {len(tracked)} text files tracked; scoring the first "
              f"{MAX_SCAN_FILES}. The rest went unread.", file=sys.stderr)
    return tracked[:MAX_SCAN_FILES]


class ScannerUnavailable(Exception):
    """The detector could not be run, so nothing has been checked.

    Raised rather than returning no findings, because those two look identical
    to every caller and only one of them means the text is clean. A gate that
    reports success when it never ran is worse than no gate: it is a gate
    everyone trusts.
    """


def scan(paths, root=None, stdin_label=None, stdin_text=None):
    """Runs the detector over paths and optional in-memory text; returns results."""
    if not paths and stdin_label is None:
        return []
    args = ["node", str(SCANNER)]
    if stdin_label is not None:
        args += ["--stdin", stdin_label]
    args += [str(p) for p in paths]
    out = run(args, cwd=root, stdin=stdin_text if stdin_label is not None else None)
    if not out:
        raise ScannerUnavailable(
            f"{SCANNER} produced nothing. Node 18+ must be on PATH and the "
            "vendored detector present."
        )
    try:
        return json.loads(out).get("results", [])
    except ValueError as err:
        raise ScannerUnavailable(f"{SCANNER} returned unreadable output: {err}") from err


def issue_key(issue):
    return f"{issue['type']}|{(issue.get('text') or '').strip().lower()}"


def blocking_issues(result, allowed=()):
    """Issues in `result` that gate, minus anything retired for that file."""
    if result.get("error"):
        return []
    found = [
        issue for issue in result.get("issues", [])
        if issue["type"] in BLOCKING_TYPES and issue_key(issue) not in allowed
    ]
    if not found and result.get("score", 0) > SCORE_CEILING:
        found = [{
            "type": "score",
            "text": f"scores {result['score']} ({result.get('verdict')}) — over the {SCORE_CEILING} ceiling",
            "severity": "high",
            "suggestion": None,
            "line": None,
        }]
    return found


def context(cwd=None):
    """Returns (root, slug, state) for an in-scope repo, or None to stand down."""
    return _lib.context(STATE_DIR, EMPTY_STATE, cwd)


def survey(root, state):
    """Returns [(result, blocking issues)] for every tracked text file."""
    allows = state.get("allows", {})
    results = scan(candidates(root), root=root)
    return [(r, blocking_issues(r, allows.get(r["label"], {}))) for r in results]


def describe(issue):
    where = f":{issue['line']}" if issue.get("line") else ""
    fix = f" -> {issue['suggestion']}" if issue.get("suggestion") else ""
    return f"{issue['type']}{where}  {issue['text']}{fix}"


def cmd_status():
    ctx = context()
    if ctx is None:
        print("Not an owned git repo; the writing pass does not apply here.")
        return 0
    root, slug, state = ctx
    rows = survey(root, state)
    if not rows:
        print(f"{slug}: no tracked text files to score.")
        return 0

    flagged = [(r, issues) for r, issues in rows if issues]
    print(f"{slug}: scored {len(rows)} text file(s).")
    for result, issues in sorted(rows, key=lambda row: -row[0].get("score", 0))[:10]:
        mark = "BLOCKED" if issues else "ok"
        print(f"  {mark:8} {result.get('score', 0):>3}  {result['label']}")
    if not flagged:
        print("\nNothing blocking.")
        return 0
    print(f"\n{len(flagged)} file(s) blocking:")
    for result, issues in flagged:
        print(f"  {result['label']}")
        for issue in issues:
            print(f"    {describe(issue)}")
    return 0


def cmd_list():
    ctx = context()
    if ctx is None:
        return 0
    root, _, state = ctx
    for result, issues in survey(root, state):
        if issues:
            print(result["label"])
    return 0


def cmd_check(argv):
    """Scores explicit paths, or --stdin text, and exits non-zero if any block."""
    ctx = context()
    root = ctx[0] if ctx else None
    allows = ctx[2].get("allows", {}) if ctx else {}

    if argv[:1] == ["--stdin"]:
        label = argv[1] if len(argv) > 1 else "input"
        results = scan([], root=root, stdin_label=label, stdin_text=sys.stdin.read())
        # Matches what the guard does with a message: no path to file an allow
        # against, so every allow the repo has recorded applies.
        allows = {label: {key for keys in allows.values() for key in keys}}
    else:
        results = scan(argv, root=root)

    blocked = False
    for result in results:
        if result.get("error"):
            print(f"{result['label']}: {result['error']}", file=sys.stderr)
            continue
        issues = blocking_issues(result, allows.get(result["label"], {}))
        state = "BLOCKED" if issues else "ok"
        print(f"{state:8} {result.get('score', 0):>3}  {result['label']}"
              f"  ({result.get('verdict', 'unscored')})")
        for issue in issues:
            print(f"    {describe(issue)}")
        blocked = blocked or bool(issues)
    return 1 if blocked else 0


def cmd_allow(argv):
    ctx = context()
    if ctx is None:
        print("Not an owned git repo; nothing to record.", file=sys.stderr)
        return 0
    _, slug, state = ctx
    if len(argv) < 3:
        print("usage: writing-pass.py allow <path> <type> <phrase> --reason \"...\"",
              file=sys.stderr)
        return 1
    if "--reason" not in argv:
        print("An allow needs a reason: why is this phrase right as written?",
              file=sys.stderr)
        return 1
    at = argv.index("--reason")
    head, reason = argv[:at], " ".join(argv[at + 1:]).strip()
    if not reason or len(head) < 3:
        print("usage: writing-pass.py allow <path> <type> <phrase> --reason \"...\"",
              file=sys.stderr)
        return 1

    path, issue_type, phrase = head[0], head[1], " ".join(head[2:])
    key = f"{issue_type}|{phrase.strip().lower()}"
    entry = state.setdefault("allows", {}).setdefault(path, {})
    entry[key] = {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    save_state(slug, state)
    print(f"Allowed {key} in {path}: {reason}")
    return 0


def cmd_allows():
    ctx = context()
    if ctx is None:
        print("Not an owned git repo; the writing pass does not apply here.")
        return 0
    _, slug, state = ctx
    allows = state.get("allows", {})
    if not allows:
        print(f"{slug}: nothing allowed.")
        return 0
    for path in sorted(allows):
        print(path)
        for key, meta in sorted(allows[path].items()):
            print(f"  {key}  — {meta.get('reason', '')}")
    return 0


def _values(tokens, i, short, long_):
    """Returns (value, tokens consumed) for a flag in any of its spellings."""
    token = tokens[i]
    if token in (short, long_):
        return (tokens[i + 1] if i + 1 < len(tokens) else None), 2
    if token.startswith(long_ + "="):
        return token[len(long_) + 1:], 1
    if short and token.startswith(short) and len(token) > len(short):
        return token[len(short):], 1
    return None, 0


def landing(command, base):
    """Returns (cwd, [(label, text)]) for a commit or PR-open, else None.

    The walk is _lib's, with the wider verb set: `gh pr edit` rewrites text a
    pull request already carries, so it lands text the same way create does.
    The texts are the commit message and PR body, read off the command line,
    since the invocation is the only place they exist.
    """
    found = _lib.landing(command, base, edit_verbs=True)
    if found is None:
        return None
    verb, at, args = found
    if verb == "commit":
        return at, message_texts(args, at, "-m", "--message", "-F", "--file")
    texts = message_texts(args, at, "-b", "--body", "-F", "--body-file")
    texts += message_texts(args, at, "-t", "--title", None, None)
    return at, texts


def message_texts(args, cwd, short, long_, file_short, file_long):
    """Collects inline and from-file message text out of a command's arguments."""
    texts = []
    i = 0
    while i < len(args):
        value, used = _values(args, i, short, long_)
        # A combined short flag such as `-am` still carries a message argument.
        if not used and short and re.fullmatch(rf"-[a-zA-Z]*{short[1]}", args[i]):
            value, used = (args[i + 1] if i + 1 < len(args) else None), 2
        if used and value:
            texts.append((long_.lstrip("-"), value))
            i += used
            continue
        if file_long:
            value, used = _values(args, i, file_short, file_long)
            if used and value:
                try:
                    texts.append((long_.lstrip("-"),
                                  (Path(cwd) / value).expanduser().read_text()))
                except OSError:
                    pass
                i += used
                continue
        i += 1
    return texts


def cmd_guard():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    found = landing(command, payload.get("cwd") or Path.cwd())
    if found is None:
        return 0
    cwd, texts = found

    ctx = context(cwd)
    if ctx is None:
        return 0
    root, _, state = ctx

    flagged = [(r["label"], issues) for r, issues in survey(root, state) if issues]

    # The message and title being written are checked as one body: a subject line
    # on its own is under the engine's ten-word floor and comes back unscorable,
    # while subject plus body is the text a reader actually sees.
    #
    # A message carries every allow the repo has recorded, not the ones filed
    # under some path. A word ruled literal in this project — `beacon` in a repo
    # that ships an analytics beacon — is literal in its commit messages too, and
    # a message is written once and gone, so there is nothing to file an allow
    # against.
    if texts:
        repo_allows = {
            key for keys in state.get("allows", {}).values() for key in keys
        }
        joined = "\n\n".join(text for _, text in texts)
        for result in scan([], root=root, stdin_label="the message being written",
                           stdin_text=joined):
            issues = blocking_issues(result, repo_allows)
            if issues:
                flagged.append((result["label"], issues))

    if not flagged:
        return 0

    listing = []
    for label, issues in flagged:
        listing.append(f"  {label}")
        listing += [f"    {describe(issue)}" for issue in issues]

    print(
        "BLOCKED: AI writing patterns in text this command would land.\n"
        "\n"
        "Rewrite the phrases below, then retry. The avoid-ai-writing skill has\n"
        "the rules and the replacements; invoke it and apply it to these files.\n"
        "\n"
        + "\n".join(listing) + "\n"
        "\n"
        "Check your work, and see everything the repo scores:\n"
        "\n"
        "  ~/.claude/tools/writing-pass.py status\n"
        "  ~/.claude/tools/writing-pass.py check <path>\n"
        "\n"
        "A phrase that is right as written — a literal term, a heading, an idiom\n"
        "the author uses — is retired once, with the reason recorded:\n"
        "\n"
        "  ~/.claude/tools/writing-pass.py allow <path> <type> <phrase> --reason \"...\"\n",
        file=sys.stderr,
    )
    return 2


# The copywriting gate.
#
# `guard` above covers copy that lands in an owned repo, which is where most of
# it ends up. Two routes go around it: copy drafted for a page and handed back
# in a reply never touches a tracked file, and copy written into a tree nobody
# here owns is outside the sweep. Both are the copywriting skill's ordinary
# output, so the skill arms a gate of its own rather than relying on the landing
# one. From the moment it is invoked, every text file written is recorded, and
# the session cannot end while any of them — or the reply carrying the copy —
# still scores blocked.
#
# What gets recorded is the path, and nothing else. Every score is taken fresh
# at the stop, for the same reason the rest of this tool keeps no ledger: a
# stored verdict is a claim about text that has since been edited.

COPY_SKILL = "copywriting"
COPY_OVERRIDE = "INTELLIGENCE_ALLOW_COPY"

# Verbs whose failure is a refusal. Both gate a landing or an ending, so a run
# that could not score is a run holding unchecked text, which is the state the
# gates exist to keep out. Arming and recording gate nothing and fail quietly.
BLOCKING_VERBS = {"guard", "guard-stop"}
VENDOR_MARKER = getattr(_lib, "VENDOR_MARKER", "VENDOR.md") if _lib else "VENDOR.md"


def hook_payload():
    """The hook event on stdin. An unreadable one is an empty one."""
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}


def hook_session(payload):
    return str(payload.get("session_id")
               or os.environ.get("CLAUDE_CODE_SESSION_ID") or "")


def copy_marker(session):
    """Where an armed gate lives, or None where it cannot have one."""
    if STATE_DIR is None or not session:
        return None
    return STATE_DIR / ("copy-%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", session))


def copy_load(session):
    marker = copy_marker(session)
    if marker is None or not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text())
    except (OSError, ValueError):
        # A marker that cannot be read is a gate that cannot be answered, so it
        # counts as absent rather than as an unanswerable refusal.
        return None


def copy_save(session, record):
    marker = copy_marker(session)
    if marker is None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(record, indent=2, sort_keys=True))


def copy_clear(session):
    marker = copy_marker(session)
    if marker is not None:
        try:
            marker.unlink()
        except OSError:
            pass


def copy_recordable(path):
    """Whether a written path is one the gate scores at the stop.

    `is_candidate` reads the parts of whatever it is handed, so an absolute path
    answers its directory, stem and suffix tests directly. The vendored set is
    the ancestors carrying a marker, which is what a repo-wide sweep would find
    for this one file.
    """
    try:
        p = Path(path)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.is_file():
            return False
        vendored = {d for d in p.parents if (d / VENDOR_MARKER).is_file()}
        return is_candidate(p, vendored)
    except OSError:
        return False


def cmd_hook_skill():
    """PostToolUse(Skill): arms the gate when the copywriting skill is invoked.

    Arming never refuses. The skill is the thing being asked for, and a gate
    that blocked the request it exists to serve would be switched off inside a
    day. Arming only makes the run answerable for what the skill produces.

    The name is matched the way the checklist engines match theirs, so a copy
    installed under a plugin prefix arms the same gate as a bare invocation.
    """
    payload = hook_payload()
    skill = str((payload.get("tool_input") or {}).get("skill") or "")
    if not re.fullmatch(r"(?:.*:)?%s" % COPY_SKILL, skill):
        return 0
    session = hook_session(payload)
    record = copy_load(session) or {
        "armed": datetime.now(timezone.utc).isoformat(), "paths": [],
    }
    copy_save(session, record)
    return 0


def cmd_hook_edit():
    """PostToolUse(Write|Edit): records a written path while the gate is armed."""
    payload = hook_payload()
    session = hook_session(payload)
    record = copy_load(session)
    if record is None:
        return 0
    path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if path and copy_recordable(path):
        resolved = str(Path(path).resolve())
        if resolved not in record.get("paths", []):
            record.setdefault("paths", []).append(resolved)
            copy_save(session, record)
    return 0


def copy_reply(payload):
    """The text of the reply the stop is ending, as one scorable body."""
    path = payload.get("transcript_path")
    if not path or not Path(path).exists():
        return ""
    try:
        text, _ = final_reply(read_transcript(path))
        return text or ""
    except Exception:
        return ""


def cmd_guard_stop():
    """Stop: refuses while copy the skill produced still scores blocked.

    The reply is scored alongside the files because a page's copy is as often
    handed back to be pasted as it is written to disk, and the landing guard
    sees neither. A reply under the detector's word floor comes back unscorable,
    which is the ordinary case for a short answer and passes.
    """
    payload = hook_payload()
    session = hook_session(payload)
    record = copy_load(session)
    if record is None:
        return 0
    if os.environ.get(COPY_OVERRIDE) == "1":
        copy_clear(session)
        return 0

    paths = [p for p in record.get("paths", []) if Path(p).is_file()]
    ctx = context()
    root = ctx[0] if ctx else None
    allows = ctx[2].get("allows", {}) if ctx else {}
    every_allow = {key for keys in allows.values() for key in keys}

    flagged = []
    try:
        for result in (scan(paths, root=root) if paths else []):
            if result.get("error"):
                continue
            issues = blocking_issues(result, allows.get(result["label"], {}))
            if issues:
                flagged.append((result["label"], issues))
        reply = copy_reply(payload)
        if reply.strip():
            for result in scan([], root=root, stdin_label="the reply being sent",
                               stdin_text=reply):
                if result.get("error"):
                    continue
                issues = blocking_issues(result, every_allow)
                if issues:
                    flagged.append((result["label"], issues))
    except ScannerUnavailable as err:
        # Unchecked is the state this gate exists to prevent, and an unrunnable
        # detector leaves the copy exactly that. It refuses and names the way
        # out rather than passing quietly.
        print(f"BLOCKED: the copywriting gate could not score the copy it holds.\n\n"
              f"  {err}\n\n"
              f"  {COPY_OVERRIDE}=1 stands the gate down.\n",
              file=sys.stderr)
        return 2

    if not flagged:
        copy_clear(session)
        return 0

    listing = []
    for label, issues in flagged:
        listing.append(f"  {label}")
        listing += [f"    {describe(issue)}" for issue in issues]

    print(
        "BLOCKED: the copy written here still reads as AI writing.\n"
        "\n"
        "The copywriting skill's closing gate is unanswered. Rewrite the phrases\n"
        "below — the avoid-ai-writing standard has the replacements — and the\n"
        "gate opens on the next stop.\n"
        "\n"
        + "\n".join(listing) + "\n"
        "\n"
        "Check your work:\n"
        "\n"
        "  ~/.claude/tools/writing-pass.py check <path>\n"
        "\n"
        "A phrase that is right as written is retired once, with the reason:\n"
        "\n"
        "  ~/.claude/tools/writing-pass.py allow <path> <type> <phrase> --reason \"...\"\n"
        "\n"
        f"  {COPY_OVERRIDE}=1 stands the gate down.\n",
        file=sys.stderr,
    )
    return 2


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    commands = {
        "status": cmd_status,
        "list": cmd_list,
        "check": lambda: cmd_check(sys.argv[2:]),
        "allow": lambda: cmd_allow(sys.argv[2:]),
        "allows": cmd_allows,
        "guard": cmd_guard,
        "hook-skill": cmd_hook_skill,
        "hook-edit": cmd_hook_edit,
        "guard-stop": cmd_guard_stop,
    }
    if mode not in commands:
        print(__doc__, file=sys.stderr)
        return 1
    if _lib is None:
        # The same fail-closed answer an unrunnable detector gets: with the
        # shared module unreadable nothing has been checked, and unchecked is
        # the state this exists to keep out of `develop`.
        print(f"BLOCKED: the writing pass could not run.\n\n"
              f"  _lib.py could not be loaded: {_LIB_ERROR}\n", file=sys.stderr)
        return 2 if mode in BLOCKING_VERBS else 1
    try:
        return commands[mode]()
    except ScannerUnavailable as err:
        # Under the hook this is a block, not a warning. An unrunnable detector
        # means the text is unchecked, and unchecked is the state this exists to
        # keep out of `develop`.
        print(f"BLOCKED: the writing pass could not run.\n\n  {err}\n", file=sys.stderr)
        return 2 if mode in BLOCKING_VERBS else 1


if __name__ == "__main__":
    sys.exit(main())
