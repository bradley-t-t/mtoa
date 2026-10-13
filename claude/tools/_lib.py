"""Shared behavior for the tools in this directory.

Every tool here runs as a standalone script: hooks and skills invoke one by
path from whatever directory the session sits in, so nothing arrives through
sys.path. A tool that needs this module loads it with importlib.util from its
own directory, falling back to the installed tree when it runs from a copy
that travelled without its siblings:

    path = Path(__file__).resolve().parent / "_lib.py"

What lives here is contract several tools must answer identically: where
per-machine state lives, what counts as an owned repository, how a per-repo
ledger file is read and written, which shell commands land work and in which
directory, how a session transcript reads back, and how a file two tools both
write is replaced and held. A tool holding a divergent variant of any of these
keeps its own copy -- this module carries only the behavior that must not
drift between callers.
"""

import contextlib
import copy
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

def _profile():
    """The profile reader, mtoa_config.py, loaded by path from beside this file.

    Same arrangement as this module itself: tools run as scripts from anywhere,
    so the reader is found next to them first and in the installed tree second.
    """
    import importlib.util
    for path in (Path(__file__).resolve().parent / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    class Absent:
        """No reader anywhere: every setting is unset."""
        @staticmethod
        def get(key, default=None):
            return default

        @staticmethod
        def owners():
            return ()
    return Absent


def setting(key, default=None):
    """One value from the owner's profile by dotted key (see mtoa_config.py)."""
    return _profile().get(key, default)


def owned_prefixes():
    """Repos under the profile's GitHub owners follow the shared workflow;
    anything else is a cloned third-party repo and is left alone."""
    return _profile().owners()

# A directory holding a VENDOR.md is a copy of someone else's work, kept here
# only so it can be re-copied when upstream moves, so its whole subtree is out
# of every pass's scope.
VENDOR_MARKER = "VENDOR.md"


def shell_tokens(command):
    """The command's tokens, tolerating a quote the shell itself would reject.

    An apostrophe inside a commit message is ordinary text to a guard reading the
    command, but strict parsing raises on it and every caller here answers that by
    giving up the walk. A walk that resolves no repository reads as one covering
    every repository, so the guard then refuses a landing in a tree it never
    examined.
    """
    try:
        return shlex.split(command)
    except ValueError:
        try:
            return [t.strip("\"'") for t in shlex.split(command, posix=False)]
        except ValueError:
            return command.split()


def state_root():
    """Where per-machine tool state lives, resolved on every call.

    A cloud session cannot write under ~/.claude (protected path); the
    state lives off it there and still syncs to the same shared remote.
    INTELLIGENCE_STATE_DIR outranks everything, which is how the case suites
    keep their fixtures out of the live state tree.
    """
    override = os.environ.get("INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return Path.home() / ".pass-state"
    return Path.home() / ".claude" / "state"


# The hold every writer of a day plan takes, and the variable saying a process
# is already inside it. A plan is loaded, changed, and written back, so two
# writers without a hold interleave into one losing the other's change. The
# variable is what lets a tool holding the lock run a tool that takes it.
PLAN_LOCK = ".day-plan-lock"
PLAN_LOCK_HELD = "INTELLIGENCE_DAYPLAN_LOCK_HELD"
PLAN_LOCK_WAIT = 20

# The hold every publisher of this tree takes. A sync reconciles a working copy
# against a clone and pushes the result, so two of them overlapping stage each
# other's half-written trees and one push reverts the other. The wait is the
# length of a clone rather than of a file write, because that is what a second
# publisher has to sit out.
SYNC_LOCK = ".claude-sync-lock"
SYNC_LOCK_HELD = "INTELLIGENCE_SYNC_LOCK_HELD"
SYNC_LOCK_WAIT = 300


def write_atomic(path, text):
    """Put a file's whole contents in place in one step.

    A reader arriving part-way through a plain write sees a truncated file, and
    two writers that overlap leave one. The bytes land in a sibling of the
    target and take its name in a single rename, so every reader sees either
    the whole previous file or the whole new one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_text(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def named_lock(state_dir, name, held_var, wait):
    """Hold the day plans for the length of one read-modify-write.

    Two sessions closing different tasks on the same plan within a minute is
    the ordinary case rather than an edge one. Without a hold the second load
    happens before the first save, and the second save carries the first
    session's task back to open. The hold spans the load and the save that
    follows it, which is where that loss happens; an atomic replace alone only
    stops a reader seeing half a file.

    A process already inside the hold passes straight through, so a tool
    holding it can run another tool that takes it. Where the hold cannot be
    taken -- a platform without flock, or a holder that outlived its work --
    the write goes ahead: the hold narrows a race, and refusing the write
    instead would lose the day's record outright. Yields whether it was taken.
    """
    if fcntl is None or os.environ.get(held_var) == "1":
        yield False
        return
    directory = Path(state_dir)
    handle, held = None, False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle = open(directory / name, "a+")
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    except OSError:
        handle = None
    was = os.environ.get(held_var)
    if held:
        os.environ[held_var] = "1"
    try:
        yield held
    finally:
        if held:
            if was is None:
                os.environ.pop(held_var, None)
            else:
                os.environ[held_var] = was
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if handle is not None:
            handle.close()


@contextlib.contextmanager
def plan_lock(state_dir, wait=PLAN_LOCK_WAIT):
    """Hold the day plans for the length of one read-modify-write."""
    with named_lock(state_dir, PLAN_LOCK, PLAN_LOCK_HELD, wait) as held:
        yield held


@contextlib.contextmanager
def sync_lock(state_dir, wait=SYNC_LOCK_WAIT):
    """Hold this tree's publishing for the length of one reconcile and push."""
    with named_lock(state_dir, SYNC_LOCK, SYNC_LOCK_HELD, wait) as held:
        yield held


def run(args, cwd=None, stdin=None):
    """Returns stdout of a command, or None when it fails."""
    try:
        done = subprocess.run(
            args, cwd=cwd, input=stdin, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def repo_root(cwd=None):
    out = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(out.strip()) if out and out.strip() else None


def origin_slug(root):
    """Returns `owner/repo` for the origin remote, or None when there isn't one."""
    out = run(["git", "remote", "get-url", "origin"], cwd=root)
    if not out:
        return None
    url = out.strip().removesuffix(".git")
    for sep in ("github.com/", "github.com:"):
        if sep in url:
            return url.split(sep, 1)[1]
    return None


def is_owned(slug):
    prefixes = owned_prefixes()
    return bool(slug) and bool(prefixes) and slug.startswith(prefixes)


def state_path(state_dir, slug):
    """The ledger file for one repo, under the calling tool's state directory."""
    return Path(state_dir) / (slug.replace("/", "__") + ".json")


def load_state(state_dir, slug, empty):
    """The recorded state for one repo, or a fresh copy of `empty`.

    `empty` is copied rather than returned, so two loads that both fall back
    cannot end up sharing one mutable template.
    """
    try:
        return json.loads(state_path(state_dir, slug).read_text())
    except (OSError, ValueError):
        return copy.deepcopy(empty)


def save_state(state_dir, slug, state):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path(state_dir, slug).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n"
    )


def context(state_dir, empty, cwd=None):
    """Returns (root, slug, state) for an in-scope repo, or None to stand down."""
    root = repo_root(cwd)
    if root is None:
        return None
    slug = origin_slug(root)
    if not is_owned(slug):
        return None
    return root, slug, load_state(state_dir, slug, empty)


def vendored_roots(paths):
    """The directories a VENDOR.md marks, each standing for its whole subtree."""
    return {Path(p).parent for p in paths if Path(p).name == VENDOR_MARKER}


def is_vendored(path, roots):
    return any(root == path or root in path.parents for root in roots)


def landing(command, base, edit_verbs=False):
    """Returns (verb, directory, arguments) for a command that lands work, else None.

    The verb is "commit" for `git commit` and "pr" for `gh pr create` -- plus
    `gh pr edit` when `edit_verbs` is set, since an edit rewrites text a pull
    request already carries. The arguments are the tokens the verb governs:
    for a commit, everything from the `commit` token on, with any `git -C`
    flags already consumed; for a PR, everything after the subcommand. Callers
    that only steer by directory use landing_cwd.

    Tokenized rather than substring-matched so a commit message that mentions
    `git commit` does not read as one. The directory matters as much as the
    verb: these commands are routinely run as `cd <other-repo> && git commit`,
    while a hook's own process stays in the session's directory throughout.
    Resolving against that would check a repo the command never touches, so
    `cd` and `git -C` are tracked as the tokens are walked, and whatever is in
    effect when the landing verb appears is what gets returned.
    """
    tokens = shell_tokens(command)

    cwd = Path(base)
    for i, token in enumerate(tokens):
        head = Path(token).name
        rest = tokens[i + 1:i + 3]
        if head == "cd" and rest[:1]:
            target = Path(rest[0]).expanduser()
            cwd = target if target.is_absolute() else cwd / target
        elif head == "git":
            # `-C` rebinds the directory for this invocation only, but it is
            # the directory the commit lands in, so it wins for this walk.
            args = tokens[i + 1:]
            at = cwd
            while args[:1] == ["-C"] and len(args) > 1:
                target = Path(args[1]).expanduser()
                at = target if target.is_absolute() else at / target
                args = args[2:]
            if args[:1] == ["commit"]:
                return "commit", at, args
        elif head == "gh" and (rest == ["pr", "create"]
                               or (edit_verbs and rest == ["pr", "edit"])):
            return "pr", cwd, tokens[i + 3:]
    return None


def landing_cwd(command, base, edit_verbs=False):
    """The directory a commit or PR-open in `command` would run in, or None."""
    found = landing(command, base, edit_verbs)
    return found[1] if found else None


def read_transcript(path):
    """The rows of a session transcript, or [] when it cannot be read."""
    try:
        return [json.loads(line) for line in open(path, errors="ignore")
                if line.strip()]
    except Exception:
        return []


FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})", re.M)


def prose_only(text):
    """A reply with its fenced blocks removed, for guards that judge writing.

    A guard reading a reply is asking about the sentences in it. A fenced block
    is neither: it is a file, a command, or a diff, reproduced because someone
    asked to see it, and its length and its wording are the source's rather than
    the writer's. Counted as prose it fails any budget; read as prose it answers
    for phrases nobody wrote -- a script that logs "the Pi is not answering"
    scans as a reply giving up on the Pi.

    An unterminated fence swallows the rest of the text, which is the safe
    reading: what follows an opening fence was meant as code whether or not the
    closing one arrived.
    """
    out, pos = [], 0
    while True:
        start = FENCE.search(text, pos)
        if not start:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:start.start()])
        marker = start.group(1)
        # Only a fence at least as long as the opener closes it, so a block
        # quoting a shorter fence stays one block.
        close = re.compile(r"^[ \t]*%s%s*[ \t]*$" % (marker[0] * len(marker), marker[0]), re.M)
        end = close.search(text, start.end())
        if not end:
            return "".join(out)
        pos = end.end()


def final_reply(rows):
    """The text of the last assistant turn, and whether that turn used tools."""
    text, used_tool = [], False
    for row in reversed(rows):
        if row.get("type") == "user":
            break
        if row.get("type") != "assistant":
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                used_tool = True
            elif block.get("type") == "text":
                text.append(block.get("text", ""))
    return "\n".join(reversed(text)), used_tool


# Lines that are page rather than prose: a quote, a heading, a table row, a
# bullet, a numbered step.
NOT_PROSE = re.compile(r"^\s*(?:[|>#]|[-*+]\s|\d+[.)]\s)")


def answer_prose(text):
    """A reply reduced to the sentences that explain it.

    Fences go first, then the page. Everything left out carries a fact that
    stands on its own -- a value in a table, a file in a list, the question in
    the quote at the top -- and a guard weighing how something was explained is
    asking about none of it. Judging the page alongside the argument charges a
    reply for the length and the vocabulary of its own evidence, which is the
    one part of it nobody wrote as prose.
    """
    return "\n".join(line for line in prose_only(text).splitlines()
                     if line.strip() and not NOT_PROSE.match(line))


# Where a skill's body announces itself in the transcript, and where a slash
# command carries the name it was invoked by.
SKILL_MARKER = re.compile(r"Base directory for this skill:.*?[/\\]skills[/\\]([\w.-]+)")
COMMAND_MARKER = re.compile(r"<command-name>\s*/?([\w.-]+)\s*</command-name>")


def row_text(row):
    """Everything a transcript row says, flattened, for marker hunting."""
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            out.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            out.append(json.dumps(block.get("input") or {}))
    return "\n".join(out)


def active_skill(rows):
    """The skill governing the reply just written, or None.

    A skill governs the turn it was invoked in and stops there. The marker
    therefore counts only when nothing plainer has been said since: a later
    ordinary prompt is the person moving on, and holding the previous skill's
    rules over that reply refuses a turn nobody asked to be shaped that way.

    Moving on is something only the person does. A meta row is the harness
    talking to itself -- a hook's refusal comes back as one -- so reading it as
    a plainer thing said since dropped the governing skill on the first rewrite
    of any reply a hook had refused, which left the rewrite of a shaped turn
    unshaped exactly where the shape was being argued about.
    """
    last_plain, last_skill, name = -1, -1, None
    for i, row in enumerate(rows):
        if row.get("type") != "user":
            continue
        text = row_text(row)
        if not text.strip():
            continue
        hit = SKILL_MARKER.search(text) or COMMAND_MARKER.search(text)
        if hit:
            last_skill, name = i, hit.group(1)
        elif not row.get("isMeta") and "<command-message>" not in text:
            last_plain = i
    return name if last_skill > last_plain else None
