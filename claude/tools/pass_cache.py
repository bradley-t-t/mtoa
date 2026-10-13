#!/usr/bin/env python3
"""Carries a checklist run's settled answers forward, keyed on content.

A gate answers a question about a project. Asked twice with neither the question
nor the project changed, it returns what it returned before, so the second sweep
buys nothing and costs a full run: the tool re-probes, and the model re-reads
every file to rule on it. Recording each answer against a fingerprint of what
produced it turns the second sweep into a lookup, and leaves only the gates whose
question or whose input actually moved.

Two fingerprints, because the two kinds of answer depend on different things.

A gate verdict depends on the gate's own definition, on the code that checks it,
and on the project state it read. Its key is the gate JSON, the whole checking
tool's content, and the scope digest. The tool enters the key whole rather than
per-checker because a checker calls helpers, and a key that reads only the
checker's own source misses a change one function away; re-running a gate sweep
costs milliseconds, so the conservative key is the cheap one.

A file ruling depends on the file and on the rules it was judged against. Its key
is the file's content and the gate registry - not the tool's plumbing, because a
ruling is the model's judgement of the file against the checklist, and editing
how the tool loads a run does not make that judgement stale. Rulings are the
expensive half: each one is a file the model read.

Answers that reach outside the project are never carried. A service is awake or
it is not, an audit is clean until a CVE lands, and a deployment serves whatever
it serves; none of that moves with the repository, so a cached pass would report
green across a change the cache cannot see. Only settled-good answers are stored
at all - an open or failing gate is work still owed, and owed work is never
served from a record.

    status [--skill S] [--target P]   what is stored, and what it would carry
    clear  [--skill S] [--target P]   drop it, so the next run sweeps everything

INTELLIGENCE_PASS_NO_CACHE=1 turns the carry-forward off everywhere.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1

# A stored answer stops being carried once it is this old even where nothing
# changed. Content hashes cover the repository; they do not cover the machine
# around it, the toolchain, or a dependency resolved at install time, and those
# drift without touching a tracked byte.
DEFAULT_TTL_DAYS = 30

# The only statuses worth storing. "open" and "fail" are work still owed, and
# carrying either would close a run over a gate nobody answered.
CARRYABLE = {"pass", "na"}


def state_root():
    override = os.environ.get("INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return Path.home() / ".pass-state"
    return Path.home() / ".claude" / "state"


CACHE_ROOT = state_root() / "pass-cache"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _age_days(stamp):
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


def ttl_days():
    raw = os.environ.get("INTELLIGENCE_PASS_CACHE_TTL_DAYS")
    try:
        return float(raw) if raw else DEFAULT_TTL_DAYS
    except ValueError:
        return DEFAULT_TTL_DAYS


def disabled():
    return os.environ.get("INTELLIGENCE_PASS_NO_CACHE") == "1"


def sha(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def hash_file(path):
    """The content hash of one file, or None where it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(131072), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def hash_paths(paths):
    """Content hashes for many files at once.

    git hashes a batch in one process where the files are in a work tree, which
    is most of the time and much faster than opening each one. The per-file walk
    is the fallback, and also the answer for anything git declines to read.
    """
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    out = {}
    root = None
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              cwd=os.path.dirname(paths[0]) or ".",
                              capture_output=True, text=True, check=False)
        if done.returncode == 0:
            root = done.stdout.strip()
    except OSError:
        root = None
    if root:
        try:
            done = subprocess.run(
                ["git", "hash-object", "--stdin-paths"], cwd=root,
                input="\n".join(paths) + "\n", capture_output=True, text=True, check=False)
            if done.returncode == 0:
                lines = [line for line in done.stdout.splitlines() if line]
                if len(lines) == len(paths):
                    out = dict(zip(paths, lines))
        except OSError:
            out = {}
    for path in paths:
        if path not in out:
            got = hash_file(path)
            if got:
                out[path] = got
    return out


def digest_of(paths, hashes=None):
    """One fingerprint for a set of files: their paths and their content."""
    hashes = hashes if hashes is not None else hash_paths(paths)
    rows = ["%s:%s" % (p, hashes.get(p, "-")) for p in sorted(hashes or paths)]
    return sha(*rows) if rows else sha("empty")


def registry_digest(gates_dir):
    """A fingerprint of the whole gate registry, so an edited rule reopens work."""
    gates_dir = Path(gates_dir)
    if not gates_dir.is_dir():
        return sha("no-registry")
    files = sorted(str(p) for p in gates_dir.glob("*.json"))
    return digest_of(files)


def tool_digest(tool_path):
    """A fingerprint of the checking tool itself, plus whatever sits beside it."""
    tool = Path(tool_path).resolve()
    files = [str(tool)]
    parent = tool.parent
    if parent.is_dir():
        files += sorted(str(p) for p in parent.glob("*.py") if p.resolve() != tool)
    return digest_of(files)


def gate_digest(gate):
    """A fingerprint of one gate's definition.

    Only what decides the answer: the rule, the check, and the conditions that
    pull the gate into a run. A reworded fix line changes what a person reads
    when the gate fails and changes nothing about whether it does.
    """
    keep = {k: gate.get(k) for k in
            ("id", "rule", "check", "when", "kinds", "severity", "requires", "flags")
            if gate.get(k) is not None}
    return sha(json.dumps(keep, sort_keys=True))


def repo_of(path):
    """The repository a path belongs to, or the path itself when it is in none.

    --git-common-dir rather than --show-toplevel: inside a worktree the latter
    answers with the worktree, which is cut fresh for every sweep, so each one
    took a bucket of its own and nothing carried forward.
    """
    probe = Path(path).expanduser().resolve()
    try:
        found = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return probe
    if found.returncode != 0 or not found.stdout.strip():
        return probe
    common = Path(found.stdout.strip())
    if not common.is_absolute():
        common = (probe / common).resolve()
    return common.parent


def key_for(skill, targets):
    joined = "|".join(sorted(str(repo_of(t)) for t in (targets or [])))
    return "%s-%s" % (skill, sha(joined)[:16])


class Cache:
    """The stored answers for one skill against one set of targets."""

    def __init__(self, skill, targets, gates_dir, tool_path, network=(), self_settling=()):
        self.skill = skill
        self.targets = [str(Path(t).expanduser().resolve()) for t in (targets or [])]
        roots = {repo_of(t) for t in self.targets}
        self.repo_root = roots.pop() if len(roots) == 1 else None
        # Longest first, so a target nested inside another names the file.
        bases = sorted(self.targets, key=len, reverse=True)
        if self.repo_root is not None:
            bases.append(str(self.repo_root))
        self.slot_bases = [Path(b) for b in bases]
        self.path = CACHE_ROOT / skill / ("%s.json" % key_for(skill, self.targets))
        self.network = set(network or ())
        self.self_settling = set(self_settling or ())
        self.registry = registry_digest(gates_dir)
        self.tool = tool_digest(tool_path)
        self.scope = None
        self.question = None
        self.served = {"gates": [], "files": []}
        self.data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        if data.get("version") != VERSION:
            data = {}
        data.setdefault("version", VERSION)
        data.setdefault("skill", self.skill)
        data.setdefault("targets", self.targets)
        data.setdefault("gates", {})
        data.setdefault("files", {})
        return data

    # ------------------------------------------------------------ the question

    def set_scope(self, paths, hashes=None):
        """Records the project state this run is asking about."""
        self.scope = digest_of(list(paths or []), hashes)
        return self.scope

    def set_question(self, kind=None, flags=(), url=None, scope=None):
        """Records what is being asked, beyond the files.

        A run over the same tree with a different kind, a different declared
        flag, or a different URL is a different question, and the answer to one
        is not the answer to the other.
        """
        self.question = sha(kind or "", "|".join(sorted(flags or ())), url or "", scope or "")
        return self.question

    def ready(self):
        return not disabled() and self.scope is not None and self.question is not None

    # ------------------------------------------------------------ gate answers

    def cacheable(self, gate):
        """Whether a gate's answer may be carried at all."""
        check = gate.get("check") or {}
        if check.get("cache") == "never" or gate.get("cache") == "never":
            return False
        kind = check.get("type")
        if kind in self.self_settling:
            return False
        if kind in self.network:
            return False
        return True

    def _gate_key(self, gate, hand=False):
        """What an answer to this gate depended on.

        A swept answer depends on the code that swept it, so the tool enters the
        key. An answer given by hand does not: nothing in the tool produced it,
        and re-running the tool cannot change it. Keying a hand answer on the
        tool would throw away the run's most expensive work every time a
        checker two thousand lines away was edited.
        """
        parts = [gate_digest(gate), self.scope, self.question]
        if not hand:
            parts.append(self.tool)
        return sha(*parts)

    def carry_gate(self, gate, hand=False):
        """The stored answer for this gate, or None when it must be worked out."""
        if not self.ready() or not self.cacheable(gate):
            return None
        row = self.data["gates"].get(gate["id"])
        if not row or row.get("key") != self._gate_key(gate, hand):
            return None
        if row.get("status") not in CARRYABLE:
            return None
        age = _age_days(row.get("at"))
        if age is None or age > ttl_days():
            return None
        self.served["gates"].append(gate["id"])
        return row

    def record_gate(self, gate, status, note, hand=False, run_id=""):
        if not self.ready() or not self.cacheable(gate):
            return
        if status not in CARRYABLE:
            self.data["gates"].pop(gate["id"], None)
            return
        self.data["gates"][gate["id"]] = {
            "key": self._gate_key(gate, hand), "status": status,
            "note": note or "", "at": now(), "by": "hand" if hand else "sweep",
            "run": run_id,
        }

    # ------------------------------------------------------------ file rulings

    def _file_key(self, content_hash):
        return sha(content_hash, self.registry)

    def _file_slot(self, path):
        """The name a ruling is filed under: relative to the repository.

        The absolute path carries the worktree that produced it, and worktrees
        are cut per sweep, so a ruling written in one was unreachable from the
        next. A worktree sits outside the repository directory rather than
        inside it, so the name is measured against the tree being swept first
        and the repository only after. Anything under neither keeps its
        absolute path, which is the only stable name it has.
        """
        probe = Path(path).expanduser().resolve()
        for base in self.slot_bases:
            try:
                return probe.relative_to(base).as_posix()
            except ValueError:
                continue
        return str(probe)

    def carry_file(self, path, content_hash):
        """The stored ruling for one file, or None when it owes a read."""
        if disabled() or not content_hash:
            return None
        slot = self._file_slot(path)
        row = self.data["files"].get(slot)
        if row is None:
            # A bucket written before rulings were filed by repository-relative
            # name still answers to the absolute one.
            row = self.data["files"].get(str(path))
        if not row or row.get("key") != self._file_key(content_hash):
            return None
        age = _age_days(row.get("at"))
        if age is None or age > ttl_days():
            return None
        self.served["files"].append(str(path))
        return row

    def record_file(self, path, content_hash, status, note, run_id=""):
        if disabled() or not content_hash or status in ("open", ""):
            return
        self.data["files"].pop(str(path), None)
        self.data["files"][self._file_slot(path)] = {
            "key": self._file_key(content_hash), "status": status,
            "note": note or "", "at": now(), "run": run_id,
        }

    # ------------------------------------------------------------ persistence

    def forget_missing(self, live_paths):
        """Drops rulings for files the project no longer has."""
        live = {str(p) for p in live_paths}
        live |= {self._file_slot(p) for p in live_paths}
        for path in list(self.data["files"]):
            if path not in live:
                self.data["files"].pop(path)

    def save(self):
        if disabled():
            return
        self.data["targets"] = self.targets
        self.data["saved"] = now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")

    def summary(self):
        gates = sorted(set(self.served["gates"]))
        files = sorted(set(self.served["files"]))
        if not gates and not files:
            return ""
        bits = []
        if gates:
            bits.append("%d gate(s) carried forward" % len(gates))
        if files:
            bits.append("%d file(s) already ruled on and unchanged" % len(files))
        return "; ".join(bits)


def open_for(skill, targets, gates_dir, tool_path, network=(), self_settling=()):
    """A cache for one skill and target set, or None where it cannot be used."""
    try:
        return Cache(skill, targets, gates_dir, tool_path, network, self_settling)
    except Exception:
        # A cache that cannot be built must never stop a run: the answer without
        # it is the answer with every gate swept, which is where this started.
        return None


# ---------------------------------------------------------------- command line


def _parse(argv):
    flags = {}
    key = None
    for item in argv:
        if item.startswith("--"):
            key = item[2:]
            flags.setdefault(key, [])
        elif key:
            flags[key].append(item)
    return flags


def _files(skill=None):
    if not CACHE_ROOT.is_dir():
        return []
    root = CACHE_ROOT / skill if skill else CACHE_ROOT
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.json") if not skill else root.glob("*.json"))


def cmd_status(argv):
    flags = _parse(argv)
    skill = (flags.get("skill") or [None])[0]
    target = (flags.get("target") or [None])[0]
    rows = _files(skill)
    if target:
        want = str(Path(target).expanduser().resolve())
        rows = [p for p in rows if want in json.loads(p.read_text()).get("targets", [])]
    if not rows:
        print("Nothing stored yet. Every gate and every file would be swept.")
        return 0
    ttl = ttl_days()
    for path in rows:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        gates = data.get("gates", {})
        files = data.get("files", {})
        fresh = sum(1 for r in gates.values()
                    if (_age_days(r.get("at")) or ttl + 1) <= ttl)
        fresh_files = sum(1 for r in files.values()
                          if (_age_days(r.get("at")) or ttl + 1) <= ttl)
        print("%-8s %s" % (data.get("skill", "?"), ", ".join(data.get("targets", []))))
        print("    %d gate answer(s) stored, %d still inside the %g-day window"
              % (len(gates), fresh, ttl))
        print("    %d file ruling(s) stored, %d still inside the window"
              % (len(files), fresh_files))
        print("    last written %s" % data.get("saved", "never"))
    if disabled():
        print("\nINTELLIGENCE_PASS_NO_CACHE=1 is set: nothing would be carried.")
    return 0


def cmd_clear(argv):
    flags = _parse(argv)
    skill = (flags.get("skill") or [None])[0]
    target = (flags.get("target") or [None])[0]
    gone = 0
    for path in _files(skill):
        if target:
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if str(Path(target).expanduser().resolve()) not in data.get("targets", []):
                continue
        path.unlink(missing_ok=True)
        gone += 1
    print("Dropped %d stored answer set(s). The next run sweeps everything." % gone)
    return 0


COMMANDS = {"status": cmd_status, "clear": cmd_clear}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verb, argv = sys.argv[1], sys.argv[2:]
    if verb not in COMMANDS:
        print("unknown command %r. One of: %s" % (verb, ", ".join(sorted(COMMANDS))),
              file=sys.stderr)
        return 2
    return COMMANDS[verb](argv)


if __name__ == "__main__":
    sys.exit(main())
