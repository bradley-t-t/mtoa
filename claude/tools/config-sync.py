#!/usr/bin/env python3
"""Keeps the tracked half of ~/.claude in sync across every machine, last edit wins.

The settings, hooks, tools and skills live on every machine the owner works on,
and in the private mirror the cloud installs from (`github.mirror` in the
profile). A change on any one
should reach the others. This reconciles the local tree with intelligence: it
pushes the files this machine edited more recently and pulls the files another
machine edited more recently. The winner is the more recent EDIT, not whichever
machine synced first — edit times survive the round trip through a manifest
committed in the mirror, so a stale machine syncing late never overwrites a
newer edit elsewhere.

  sync              reconcile the tracked paths with intelligence and push back
  remove <path>     retire a path: stamp it, then delete it on both sides

Additive otherwise: a file deleted on one machine is restored from the mirror
rather than deleted everywhere, because a deletion carries no edit time to weigh
against a live edit. Deleting it from the mirror by hand does not settle it
either, since the next machine to sync still holds a copy with a real edit time.
`remove` writes the stamp that outranks that copy, and an edit made after the
stamp outranks the stamp back, so reviving something is an edit rather than a
hand-fix.

A tool is also judged by its cases before its clock: `tools/x.py` answers to
`tools/tests/x-cases.py`, and where both copies have cases to answer, one that
fails loses to one that passes however recently it was touched. That is what
stops an older build carrying a later clock from reverting newer work.

A copy edited on this machine since its last completed sync is the exception. A
tool part-way through a multi-step edit fails its own cases for as long as the
edit lasts, and demoting it there restores the mirror over work that is still
being written. So the machine an edit is happening on holds its own file: the
path is left as both sides have it, named in the run's output, and left out of
the manifest, and the mark stays where it was so the next run still reads the
edit as in flight. Once the cases pass, the file arbitrates like any other.

One publisher of this tree runs at a time. A reconcile is a clone, a merge of
two trees file by file, and a push, and two of them overlapping stage each
other's half-written trees into one commit, so the run that pushes second
carries whichever half the first had reached. `sync` and `publish` hold the
whole operation, retries included; `check` reads and holds nothing.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

HOME = Path.home()
INTELLIGENCE = HOME / ".claude"
CACHE = INTELLIGENCE / "state" / ".config-sync-repo"


def mirror_remote():
    """The private mirror this tree reconciles against, as a clone URL.

    The profile names it (`github.mirror`). A machine the profile has not reached
    yet still holds the clone an earlier sync made, and that clone's origin is the
    same mirror, so it answers next; INTELLIGENCE_REPO is the word of a first
    install that has neither. Empty means no mirror is set up, and a sync then
    stands down rather than guessing at one.
    """
    slug = None
    try:
        slug = _shared().setting("github.mirror")
    except Exception:
        slug = None
    if slug:
        return slug if "://" in slug or slug.startswith("git@") \
            else "https://github.com/%s.git" % slug.strip("/")
    cached = origin_url(CACHE)
    if cached:
        return cached
    slug = os.environ.get("INTELLIGENCE_REPO")
    return "https://github.com/%s.git" % slug.strip("/") if slug else ""


MANIFEST = ".config-sync-manifest.json"
# Notes are the one sync path that is data rather than setup. They are here
# because a note is worth nothing on the machine it was written on: the point is
# that the Pi, the laptop and a cloud container opened tomorrow all read it.
# `tools/note.py` writes one file per day per machine and never edits one twice,
# which is what keeps last-edit-wins from settling a log by throwing half of it
# away.
#
# `profile` is the owner's own half: the names, owners and hosts every tool reads
# (`profile/mtoa.json`) and the rules that are theirs alone (`profile/CLAUDE.md`,
# which CLAUDE.md imports).
SYNC_PATHS = ["skills", "checklists", "ratchet", "standards", "tools", "site-rosters",
              "notes", "profile",
              "settings.json", "CLAUDE.md"]
# The one file in the mirror a container runs rather than installs. It is not a
# sync path: a machine holds no copy of it to weigh, so `check` only asks the
# mirror whether it is still there.
CLOUD_SCRIPT = "cloud/bootstrap.sh"

# Path to the time it was retired. The sync is additive, so a file that merely
# goes missing on one machine is restored from the other: vanishing carries no
# edit time to weigh against a live edit elsewhere. A stamp is that edit time.
# A copy touched after its stamp outranks it and comes back, which is what makes
# reviving something an edit rather than a hand-fix to the mirror.
REMOVED = ".config-sync-removed.json"

# The same ledger under a second name, which some machines write instead. A
# stamp only outranks a live copy if the run that finds the copy also finds the
# stamp, so a retirement filed under a name nothing reads is a retirement that
# gets undone by the next sync. Both names are read as one ledger, and a
# retirement made here writes the name above and clears this one.
LEGACY_REMOVED = ".config-sync-retired.json"

# What each retired path's content hashed to at the moment it was stamped, in a
# sidecar so the stamp ledger above stays plain path-to-integer -- a build that
# predates this file must keep parsing that ledger, since the sync is the only
# channel that could deliver it a newer build. A checkout or a chmod bumps a
# copy's mtime past its stamp without changing a byte, which is how five
# retirements came back; a copy whose bytes still match the recorded hash is the
# retired copy whatever its clock says. Reviving is `revive`, or an actual edit.
REMOVED_SHA = ".config-sync-removed-sha.json"


def _sandbox():
    """The suite-sandbox module, loaded by path.

    A candidate copy is judged by running its cases, and a run started from here
    carries none of the fixture roots a suite sets on itself, so the sandbox is
    what marks it as a suite and what puts the session command out of its reach.
    """
    here = Path(__file__).resolve().parent / "suite-sandbox.py"
    path = here if here.is_file() else \
        Path.home() / ".claude" / "tools" / "suite-sandbox.py"
    spec = importlib.util.spec_from_file_location("intelligence_suite_sandbox",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shared():
    """The shared _lib module, loaded by path.

    Hooks and session starts run this tool as a script from arbitrary
    directories, so the module is found beside this file rather than through
    sys.path, with the installed tree as the fallback for a copy executed from
    elsewhere.
    """
    here = Path(__file__).resolve().parent / "_lib.py"
    path = here if here.is_file() else Path.home() / ".claude" / "tools" / "_lib.py"
    spec = importlib.util.spec_from_file_location("intelligence_tools_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    _lib = _shared()
    _state_root = _lib.state_root
except Exception:
    _lib = None

    def _state_root():
        # The same three answers the shared module gives, because a copy
        # running without it still has to find the state the rest of this
        # machine keeps: the override first, then a remote session's own root
        # off the config tree, then the tree's own state directory.
        override = os.environ.get("INTELLIGENCE_STATE_DIR")
        if override:
            return Path(override)
        if os.environ.get("CLAUDE_CODE_REMOTE"):
            return Path.home() / ".pass-state"
        return Path.home() / ".claude" / "state"


# The hold's file, the variable naming a process already inside it, and how long
# a second publisher waits for the first. Taken from the shared module so every
# publisher of this tree answers to one protocol, and spelled here for a copy
# running without that module. The wait is the length of a clone and a reconcile
# rather than of a file write, because that is what the second one sits out.
LOCK_FILE = getattr(_lib, "SYNC_LOCK", ".claude-sync-lock")
LOCK_HELD = getattr(_lib, "SYNC_LOCK_HELD", "INTELLIGENCE_SYNC_LOCK_HELD")
LOCK_WAIT = getattr(_lib, "SYNC_LOCK_WAIT", 300)


@contextlib.contextmanager
def sync_hold(state_dir=None, wait=None):
    """Exclusive hold over this tree's publishing, for one whole reconcile and push.

    The hold spans the clone, the merge, the commit, the push and every retry,
    so a second run starts against a mirror that has finished moving rather than
    one still moving, and stages a tree nothing else is writing into. Yields
    whether it was taken.

    A process already inside the hold passes straight through, so a publish
    started from within one runs instead of waiting for itself. Where the hold
    cannot be taken -- a platform with no flock, or a holder that outlived its
    work -- the run goes ahead anyway: the hold orders publishers, and standing
    down instead would leave this machine's edits only here, which is the loss
    it exists to prevent.
    """
    directory = Path(state_dir if state_dir is not None else _state_root())
    wait = LOCK_WAIT if wait is None else wait
    if _lib is not None:
        with _lib.sync_lock(directory, wait) as got:
            yield got
        return
    if fcntl is None or os.environ.get(LOCK_HELD) == "1":
        yield False
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle = open(directory / LOCK_FILE, "a+")
    except OSError:
        yield False
        return
    taken = False
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            taken = True
            break
        except OSError:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    was = os.environ.get(LOCK_HELD)
    if taken:
        os.environ[LOCK_HELD] = "1"
    try:
        yield taken
    finally:
        if taken:
            if was is None:
                os.environ.pop(LOCK_HELD, None)
            else:
                os.environ[LOCK_HELD] = was
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r


def ensure_repo():
    """A clone of the mirror at origin/main, reused across runs.

    Any local state in the cache is discarded rather than merged -- the cache is
    a staging area for this run, and the only copy worth keeping is the one on
    the remote. Full history, because `commit_times` reads it.
    """
    if (CACHE / ".git").is_dir():
        git("fetch", "--quiet", "origin", cwd=CACHE, check=False)
        git("reset", "--hard", "origin/main", cwd=CACHE, check=False)
        git("clean", "-fdq", cwd=CACHE, check=False)
    else:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        if CACHE.exists():
            shutil.rmtree(CACHE)
        # Keep the cache byte-identical to the mirror (LF), so Windows line-ending
        # conversion never makes files look changed and churn the sync.
        git("-c", "core.autocrlf=false", "-c", "core.eol=lf",
            "clone", "--quiet", REMOTE, str(CACHE))
        git("config", "core.autocrlf", "false", cwd=CACHE, check=False)
    return CACHE


def commit_times(repo):
    """Last commit time per repo-relative path, the fallback edit time for a
    file the manifest has not recorded yet. Needs full history, not a shallow
    clone."""
    out = {}
    r = git("log", "--format=@@@%ct", "--name-only", "--no-renames", cwd=repo, check=False)
    ct = None
    for line in r.stdout.splitlines():
        if line.startswith("@@@"):
            ct = int(line[3:])
        elif line and ct is not None:
            out.setdefault(line, ct)
    return out


# .trash is the CLI's own recycling for replaced skill versions -- machine-local
# debris, not skills, and it once put 408 files on the mirror.
# `synced` holds the skills the harness installs and manages per machine.
# They are not this setup and mirroring them is 638 files of churn.
IGNORE_DIRS = {".git", "__pycache__", ".trash", "synced"}
IGNORE_NAMES = {".DS_Store", "Thumbs.db"}
IGNORE_SUFFIX = {".pyc", ".pyo", ".swp", ".bak", ".orig", ".rej", ".tmp"}


def syncable(rel):
    parts = rel.split("/")
    name = parts[-1]
    return not (
        set(parts) & IGNORE_DIRS
        or name in IGNORE_NAMES
        or name.startswith("._")  # macOS AppleDouble sidecars
        or any(name.endswith(s) for s in IGNORE_SUFFIX)
    )


def walk(root, base):
    """Map repo-relative path -> absolute path for every syncable file under
    root/base. Machine-local junk is left out; `syncable` lists what that
    covers. A base naming a single file maps just that file."""
    out = {}
    b = root / base
    if not b.exists():
        return out
    if b.is_file():
        rel = str(b.relative_to(root)).replace(os.sep, "/")
        return {rel: b} if syncable(rel) else out
    for p in b.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        if syncable(rel):
            out[rel] = p
    return out


def is_binary(b):
    return b"\x00" in b[:8192]


def norm(b):
    """LF-normalized bytes for text; raw bytes for binary (fonts, images), which
    a normalize would corrupt. Keeps CRLF-vs-LF from ever looking like a change."""
    return b if is_binary(b) else b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def read_norm(path):
    return norm(path.read_bytes())


def copy_mode(src, dst):
    """Carry the executable bit across. scripts/ and tools/ are run as hook
    commands, so a copy that lands without +x breaks every session that pulls
    it, silently and on every tool call."""
    try:
        if os.access(src, os.X_OK):
            os.chmod(dst, os.stat(dst).st_mode | 0o111)
    except OSError:
        pass


def same(a, b):
    try:
        return read_norm(a) == read_norm(b)
    except OSError:
        return False


def cases_for(root, rel):
    """The cases file that answers for a synced tool, or None where none does.

    Same convention `tools/tests/cases.py` uses: `tools/x.py` answers to
    `tools/tests/x-cases.py`. Only tools are covered. A skill is prose and a
    settings file is data, and neither has a way to be run and found wanting.
    """
    if "/tests/" in rel or not rel.endswith(".py"):
        return None
    if rel.startswith("tools/"):
        name = rel[len("tools/"):-len(".py")] + "-cases.py"
    elif rel.startswith("scripts/"):
        # Namespaced so a script and a tool sharing a name can never claim the
        # same cases file.
        name = "scripts-" + rel[len("scripts/"):-len(".py")].replace("/", "-") + "-cases.py"
    else:
        return None
    path = root / "tools" / "tests" / name
    return path if path.is_file() else None


def passes_cases(root, rel, candidate):
    """Whether a candidate copy answers its cases, or None where it has none.

    A copy is judged by the cases sitting beside it, so each side is held to what
    its own tree says the tool has to do. None where the cases cannot be run at
    all: an unreadable or hanging cases file is not evidence against the copy,
    and treating it as evidence would let a broken test discard working work.
    """
    cases = cases_for(root, rel)
    if cases is None:
        return None
    env = dict(os.environ, INTELLIGENCE_TEST_TARGET=str(candidate))
    try:
        code, _, _ = _sandbox().run_sandboxed(cases, env, timeout=CASE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return code == 0


# Long enough for a real set of cases, short enough that one that hangs cannot
# stall a session's opening sync.
CASE_TIMEOUT = 60


def load_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def last_sync_at():
    """When a sync last completed for this tree, or 0 where none has.

    The fast path's own record answers this, so an edit is weighed against the
    same mark that decides whether a run walks the tree at all rather than
    against a second one kept alongside it. Zero says nothing either way, which
    leaves the cases tiebreak deciding as it did before any run recorded a mark.
    """
    return int(load_json(LAST).get("at") or 0)


def load_removals(repo):
    """Every retirement, read from both ledger files as one.

    Where the two name a path, the later stamp wins, so a retirement restated
    after a revival still outranks the copy that was revived.
    """
    removed = load_json(repo / REMOVED)
    for rel, when in load_json(repo / LEGACY_REMOVED).items():
        if int(when or 0) > int(removed.get(rel) or 0):
            removed[rel] = when
    return removed


def retired(removed, rel, when):
    """Whether a retirement stamp still outranks a copy last edited at `when`.

    A stamp covers the path it names and everything under it, so retiring a skill
    is one entry rather than one per file it happens to hold.
    """
    for path, stamp in removed.items():
        if rel == path or rel.startswith(path.rstrip("/") + "/"):
            if when <= int(stamp):
                return True
    return False


def norm_sha(path):
    try:
        return hashlib.sha256(read_norm(path)).hexdigest()
    except OSError:
        return None


def retired_copy(removed, shas, rel, when, path):
    """Whether this particular copy is the retired one.

    The clock test alone is defeatable: a checkout or a chmod hands a retired
    copy an mtime past its stamp without changing a byte, and the sync reads
    that as an edit and restores it. Content settles it -- a copy whose bytes
    still hash to what was recorded at stamping is the retired copy however
    recent its clock, and only a copy that actually differs counts as the edit
    that revives the path.
    """
    for stamped, stamp in removed.items():
        if rel == stamped or rel.startswith(stamped.rstrip("/") + "/"):
            sub = "" if rel == stamped else rel[len(stamped.rstrip("/")) + 1:]
            recorded = (shas.get(stamped) or {}).get(sub)
            if recorded and path is not None and norm_sha(path) == recorded:
                return True
            if when <= int(stamp):
                return True
    return False


def drop(path):
    try:
        path.unlink()
    except OSError:
        return
    parent = path.parent
    while parent != parent.parent:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


# A private key block, and a URL carrying a password in its authority. Both are
# credentials that a clone hands to whoever runs it.
#
# The key block is matched with its body, because the shape alone appears in
# every fixture and every pattern that looks for one. A real key body is hundreds
# of base64 characters; the short stand-ins in a cases file are not keys, and
# withholding those would cost the tool the cases that settle its own sync races.
KEY_BLOCK = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----(.*?)-----END", re.S)
KEY_BODY_MIN = 200

URL_PASSWORD = re.compile(r"://[^\s/:@]+:([^\s/@]+)@")

# A password is a literal or it is not a password. `${token}` in a clone command
# and a bracketed run inside a regex both sit in the same position and neither is
# a secret, so anything carrying shell or pattern syntax is read as code.
NOT_LITERAL = re.compile(r"[$\\{}\[\]()<>*^\"'`|]")

# Secrets named in the working agreement as belonging to the environment's own
# variable settings rather than to a tracked file. A long-lived login token
# authenticates the account itself rather than a scoped service, so a copy in
# the mirror is a copy of the login.
NAMED_SECRETS = re.compile(
    r"\b(CRYPTOFORT_MASTER_KEY|SUPABASE_SERVICE_ROLE_KEY|CLAUDE_CODE_OAUTH_TOKEN)"
    r"\b\s*[=:]\s*[\"']?([^\s\"',]+)")

# The sync's own credential, which travels with settings.json on purpose: it is
# scoped, it is short-lived, and the sync cannot reach a private mirror without
# it. A URL whose password is one of these is the credential helper's own entry.
GH_TOKEN_SHAPE = re.compile(r"^(gh[pousr]_|github_pat_)")


def secret_shape(path):
    """The credential a file carries that must not reach the mirror, or None.

    The mirror is a git repository every machine and every container clones, so a
    credential committed to it is published rather than stored. A file that
    matches stays on the machine that holds it and is never copied across, which
    means blocking one can only ever cost the mirror a file, never a machine.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for body in KEY_BLOCK.findall(text):
        if len(re.sub(r"[^A-Za-z0-9+/=]", "", body)) >= KEY_BODY_MIN:
            return "a private key block"
    found = NAMED_SECRETS.search(text)
    if found and not NOT_LITERAL.search(found.group(2)):
        return "%s with a value" % found.group(1)
    for password in URL_PASSWORD.findall(text):
        if len(password) >= 6 and not NOT_LITERAL.search(password) \
                and not GH_TOKEN_SHAPE.match(password):
            return "a URL carrying a password"
    return None


def reconcile(repo, manifest, commits, removed=None, held=None, shas=None, seen=None,
              skipped=None):
    """Merge the local tree and the mirror file by file, later edit winning.

    Mutates both trees and the manifest, and returns (pushed, pulled). A file on
    one side only is copied to the other rather than treated as a deletion: a
    deleted file carries no edit time, so it can never win a comparison against
    one that does.

    A path the cases would demote here, but which this machine has edited since
    its last completed sync, is settled by neither test: it is appended to
    `skipped` and both copies are left as they are.

    The edit time on the mirror's side is the later of what the manifest records
    and when the file was last committed there, which is what makes a change
    landed by pull request outrank an older copy on a machine that has not
    synced since.

    A path carrying a retirement stamp is the exception to additive: it is deleted
    on whichever side still holds it, unless that copy was edited after the stamp.
    """
    removed = removed or {}
    shas = shas or {}
    held = held if held is not None else []
    seen = seen if seen is not None else set()
    skipped = skipped if skipped is not None else []
    since = last_sync_at()
    local, remote = {}, {}
    for base in SYNC_PATHS:
        local.update(walk(INTELLIGENCE, base))
        remote.update(walk(repo, base))
    pushed = pulled = 0
    for rel in sorted(set(local) | set(remote)):
        seen.add(rel)
        lp, rp = local.get(rel), remote.get(rel)
        # A local file carrying a credential is left where it is, and the mirror's
        # copy of that path is left alone too. Pulling over it would answer a
        # blocked push by overwriting the file the block was protecting.
        if lp is not None:
            shape = secret_shape(lp)
            if shape:
                held.append((rel, shape))
                continue
        newest = max(int(p.stat().st_mtime) for p in (lp, rp) if p)
        newest = max(newest, int(manifest.get(rel) or 0), int(commits.get(rel) or 0))
        # Retired only when every existing copy is the retired one; a copy whose
        # bytes actually differ from what was stamped is the edit that revives.
        stamped = any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in removed)
        if stamped and all(retired_copy(removed, shas, rel, newest, p)
                           for p in (lp, rp) if p):
            for path in (lp, rp):
                if path:
                    drop(path)
            manifest.pop(rel, None)
            continue
        if lp and rp:
            if same(lp, rp):
                # Content-equal copies can still disagree on the executable bit,
                # and a hook invoked by bare path dies without it. Level the bit
                # up on both sides so mode-only drift heals instead of hiding
                # behind the content short-circuit.
                copy_mode(lp, rp)
                copy_mode(rp, lp)
                continue
            l_mt = int(lp.stat().st_mtime)
            # The newest of what the mirror knows, not the first of them. A file
            # edited in the mirror directly — a pull request landing on main — is
            # recorded by its commit and by nothing else, because no machine's
            # ~/.claude was touched and so no manifest entry moved. Reading the
            # manifest alone then hands the round to whichever machine happens to
            # carry a later mtime on a file it never edited, and the merge is
            # reverted by a sync that reports itself as a push. Taking the max
            # means a commit to the mirror always outranks a stale local copy.
            r_mt = max(int(manifest.get(rel) or 0),
                       int(commits.get(rel) or 0)) or int(rp.stat().st_mtime)
            # Cases before the clock. An older build carrying a later mtime wins
            # every comparison time can make, and the sync reports the revert as
            # a push. Where a tool has cases, a copy that answers them beats one
            # that does not, whichever was touched last; where both answer or
            # neither does, the clock decides as before.
            here, there = (passes_cases(INTELLIGENCE, rel, lp),
                           passes_cases(repo, rel, rp))
            if here is not None and there is not None and here != there:
                # An edit in progress fails its cases the same way an older build
                # does, and the difference between them is the mark: a local copy
                # touched since this machine's last completed sync is work being
                # written here, not a build that arrived stale. A publish fires
                # from the edit itself, so a multi-step change is read half-written
                # by the run its own first edit scheduled, and demoting it there
                # restores the mirror over the rest of the change. The machine an
                # edit is happening on holds its own file: neither copy is written,
                # neither is stamped, and the run names the path. A copy no newer
                # than the mark carries no edit of this machine's and loses to a
                # passing mirror exactly as before.
                if not here and since and l_mt > since:
                    skipped.append(rel)
                    continue
                local_wins = here
            else:
                local_wins = l_mt >= r_mt
            if local_wins:
                rp.write_bytes(read_norm(lp))
                copy_mode(lp, rp)
                manifest[rel] = l_mt
                pushed += 1
            else:
                lp.write_bytes(read_norm(rp))
                copy_mode(rp, lp)
                os.utime(lp, (r_mt, r_mt))
                pulled += 1
        elif lp:
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(read_norm(lp))
            copy_mode(lp, dst)
            manifest[rel] = int(lp.stat().st_mtime)
            pushed += 1
        else:
            dst = INTELLIGENCE / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(read_norm(rp))
            copy_mode(rp, dst)
            mt = int(manifest.get(rel) or commits.get(rel) or rp.stat().st_mtime)
            os.utime(dst, (mt, mt))
            pulled += 1
    return pushed, pulled


def publish(repo, subject):
    """Commit and push the mirror. True when the push landed, False when it did not.

    Dropping the commit on a rejected push loses the edits the run just merged in,
    and the next run reads the mirror's older copies as the newer side and pulls
    them back over the machine that made them. The caller reconciles against the
    moved mirror and tries again, so a rejection costs a round trip rather than
    the work.
    """
    git("add", "-A", cwd=repo)
    if not git("status", "--porcelain", cwd=repo).stdout.strip():
        return True
    git("commit", "--quiet", "-m", subject, cwd=repo)
    r = subprocess.run(["git", "push", "--quiet", "origin", "main"],
                       cwd=repo, capture_output=True, text=True)
    return r.returncode == 0


ATTEMPTS = 4


def newest_local_mtime():
    """The latest mtime under the synced paths, directories included.

    Directory mtimes are what catch a deletion: removing a file changes no
    file's clock, only its parent's.
    """
    latest = 0
    for base in SYNC_PATHS:
        b = INTELLIGENCE / base
        if b.is_file():
            latest = max(latest, int(b.stat().st_mtime))
            continue
        if not b.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(b):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            names = [dirpath] + [os.path.join(dirpath, n) for n in filenames
                                 if syncable(n)]
            for p in names:
                try:
                    latest = max(latest, int(os.stat(p).st_mtime))
                except OSError:
                    pass
    return latest


def in_step_fast():
    """Whether nothing moved since the last recorded sync, cheaply.

    One ls-remote against the mirror and one stat walk with no hashing, no
    fetch, and no reset. Either the mirror's head moved or something local was
    touched after the last sync started; otherwise a full reconcile would walk
    900 files to change nothing, at every session start on every machine.
    """
    data = load_json(LAST)
    head, at = data.get("head"), int(data.get("at") or 0)
    if not head or not at:
        return False
    r = git("ls-remote", REMOTE, "refs/heads/main", check=False)
    remote_head = (r.stdout.split() or [""])[0]
    return bool(remote_head) and remote_head == head and newest_local_mtime() <= at


HOOK_PATH = re.compile(r"^(?:/Users|/home)/[^/\s]+/(\S+)(?:\s+(.*))?$")
# A tool that writes its own row into the settings file writes it already
# guarded, with its own machine's home baked into both halves. That shape is not
# a raw command, so the pattern above passes over it and the row survives every
# sync still naming one desk's home.
HOOK_GUARDED = re.compile(
    r'^\[\s*-[ex]\s+"?(?:/Users|/home)/[^/\s"]+/(\S+?)"?\s*\]\s*\|\|\s*exit\s+0;\s*'
    r'exec\s+"?(?:/Users|/home)/[^/\s"]+/\S+?"?(?:\s+(.*))?$')


def portable_hooks():
    """Rewrite settings.json hook commands that name one machine's home.

    A hook command is run verbatim wherever settings.json lands, and this file
    lands in every cloud container. One naming an absolute path under a home
    directory exits 127 there, which the harness reads as a refusal, so a
    PreToolUse hook wired that way stops every tool call a routine makes and the
    run hangs waiting for an approval nobody is there to give. Four of them did
    exactly that. The guarded form runs the same program where it is installed
    and stands down where it is not, so a tool that lives on one machine cannot
    take the others down with it.

    The test is existence rather than executability on purpose. A guard whose
    program is present and unrunnable is a broken install, and standing down on
    that is how every check on a machine switches itself off without saying so;
    the exec fails loudly instead.
    """
    path = INTELLIGENCE / "settings.json"
    if not path.is_file():
        return []
    try:
        body = json.loads(path.read_text())
    except ValueError:
        return []
    moved = []
    for phase, entries in (body.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks") or []:
                command = hook.get("command", "")
                match = HOOK_PATH.match(command) or HOOK_GUARDED.match(command)
                if not match:
                    continue
                tail, args = match.group(1), match.group(2) or ""
                rebuilt = ('[ -e "$HOME/%s" ] || exit 0; exec "$HOME/%s"%s'
                           % (tail, tail, (" " + args) if args else ""))
                if rebuilt == command:
                    continue
                hook["command"] = rebuilt
                moved.append("%s: %s" % (phase, tail))
    # A tool that rewires itself reads its own guarded entry as absent and adds
    # the raw one back, so normalising alone leaves two entries running the same
    # guard and reporting the same verdict twice.
    for phase, entries in (body.get("hooks") or {}).items():
        for entry in entries:
            hooks = entry.get("hooks") or []
            seen, kept = set(), []
            for hook in hooks:
                command = hook.get("command", "")
                if command and command in seen:
                    moved.append("%s: dropped a second copy of one hook" % phase)
                    continue
                seen.add(command)
                kept.append(hook)
            hooks[:] = kept
    if moved:
        path.write_text(json.dumps(body, indent=2) + "\n")
    return moved


def origin_url(path):
    """A checkout's origin URL, read rather than shelled out for.

    Scanning a directory of candidates costs a stat and a small read each this
    way, which is what makes it reasonable to look on every sync.
    """
    try:
        text = (path / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    remote = re.search(r'\[remote "origin"\][^\[]*', text, re.S)
    if not remote:
        return None
    url = re.search(r"^\s*url\s*=\s*(\S+)", remote.group(0), re.M)
    return url.group(1) if url else None


REMOTE = mirror_remote()


def same_repo(a, b):
    norm = lambda u: re.sub(r"\.git$", "", (u or "").strip().rstrip("/")).lower()
    return bool(norm(a)) and norm(a) == norm(b)


def display(path):
    return str(path).replace(str(HOME), "~", 1)


def mirror_checkouts():
    """Every hand clone of the mirror on this machine.

    The sync clones into its own cache and pushes from there, so a clone
    somebody reads and edits by hand is never touched by it and falls behind in
    silence. That is the worst shape for a stale tree to be in, because it reads
    as the mirror's current state to anyone who opens it.

    Found by matching origin rather than by a hardcoded path, so moving the
    checkout keeps it covered and a machine holding none does nothing.
    CONFIG_SYNC_CHECKOUTS names any that live outside the two roots walked here.
    """
    candidates = [Path(raw.strip()).expanduser()
                  for raw in (os.environ.get("CONFIG_SYNC_CHECKOUTS") or "").split(os.pathsep)
                  if raw.strip()]
    for root in (HOME / "Development", HOME):
        try:
            candidates.extend(child for child in root.iterdir() if child.is_dir())
        except OSError:
            continue
    skip = {CACHE.resolve(), INTELLIGENCE.resolve()}
    found, seen = [], set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or resolved in skip:
            continue
        seen.add(resolved)
        if same_repo(origin_url(resolved), REMOTE):
            found.append(resolved)
    return found


def refresh_checkouts():
    """Fast-forward every hand clone of the mirror, and never do more than that.

    A clone carrying its own commits or an edited file is somebody mid-thought,
    so it is reported and left exactly as it stands. Only a clean clone that is
    purely behind is moved, and only by a fast-forward, which is what makes this
    safe to run unattended on every sync.

    Nothing here can fail the sync. A checkout that cannot be read is one
    checkout's problem, and the push has already landed by the time this runs.
    """
    for path in mirror_checkouts():
        try:
            # Tracked changes only. An untracked scratch file cannot be lost to a
            # fast-forward, and counting it would park a checkout out of date
            # forever over a stray .DS_Store. Where an incoming commit really does
            # collide with an untracked path, git refuses the merge itself and the
            # returncode below reports it.
            if git("status", "--porcelain", "--untracked-files=no",
                   cwd=path, check=False).stdout.strip():
                print("config-sync: %s has uncommitted work, left alone" % display(path))
                continue
            git("fetch", "--quiet", "origin", cwd=path, check=False)
            upstream = git("rev-parse", "--abbrev-ref", "@{u}",
                           cwd=path, check=False).stdout.strip()
            if not upstream:
                print("config-sync: %s tracks no branch, left alone" % display(path))
                continue
            counts = git("rev-list", "--left-right", "--count", "HEAD...%s" % upstream,
                         cwd=path, check=False).stdout.split()
            if len(counts) != 2:
                continue
            ahead, behind = int(counts[0]), int(counts[1])
            if ahead:
                print("config-sync: %s is %d commit%s ahead of the mirror, left alone"
                      % (display(path), ahead, "" if ahead == 1 else "s"))
                continue
            if not behind:
                continue
            if git("merge", "--ff-only", "--quiet", upstream, cwd=path, check=False).returncode:
                print("config-sync: %s would not fast-forward, left alone" % display(path))
            else:
                print("config-sync: %s moved %d commit%s up to the mirror"
                      % (display(path), behind, "" if behind == 1 else "s"))
        except Exception as problem:
            print("config-sync: %s could not be refreshed (%s)" % (display(path), problem))


def sync(force=False):
    started = int(time.time())
    for moved in portable_hooks():
        print("config-sync: made a hook portable (%s)" % moved)
    if not force and in_step_fast():
        print("config-sync: in step (nothing moved since the last sync)")
        # Drift is not always this machine's doing: another machine pushing
        # leaves a hand checkout here behind with nothing of ours to send.
        refresh_checkouts()
        return []
    held = []
    # Held across every attempt rather than around each one. A hold taken per
    # attempt lets a second run clone between them, and both then merge into
    # trees the other is still writing.
    with sync_hold():
        for attempt in range(ATTEMPTS):
            # ensure_repo resets the cache onto the current mirror, so each attempt
            # reconciles against where the mirror actually is rather than where it was
            # when the rejected attempt started.
            repo = ensure_repo()
            manifest = load_json(repo / MANIFEST)
            removed = load_removals(repo)
            held = []
            seen = set()
            skipped = []
            pushed, pulled = reconcile(repo, manifest, commit_times(repo), removed, held,
                                       load_json(repo / REMOVED_SHA), seen, skipped)
            # `seen` is every path either side actually holds, so an entry outside it
            # records a file that exists nowhere -- a ghost that would otherwise sit
            # in the manifest forever.
            manifest = {k: v for k, v in manifest.items() if syncable(k) and k in seen}
            json.dump(manifest, open(repo / MANIFEST, "w"), indent=0, sort_keys=True)
            # The mirror is the one repo written straight to main rather than through a
            # pull request. It holds no product code and this runs at session start on
            # every machine, so a review step would only stall the sync behind a human.
            if publish(repo, "Config sync: %d pushed, %d pulled" % (pushed, pulled)):
                print("config-sync: %d pushed, %d pulled" % (pushed, pulled))
                for rel, shape in held:
                    print("config-sync: %s stays on this machine (%s)" % (rel, shape))
                for rel in skipped:
                    print("config-sync: %s is left as both sides hold it (edited "
                          "here since the last sync, and its cases do not pass "
                          "yet)" % rel)
                mark(PUBLISHED)
                head = git("rev-parse", "HEAD", cwd=repo, check=False).stdout.strip()
                if head:
                    # Stamped at the run's start, not its end: an edit landing while
                    # the reconcile walked has a clock past this and forces the next
                    # run to look properly.
                    #
                    # A run that skipped a path did not complete for it, so the mark
                    # stays where it was. Moving it past the edit that earned the
                    # skip hands the next run a copy that reads as settled, and that
                    # run reverts exactly the work this one protected.
                    at = last_sync_at() if skipped else started
                    json.dump({"head": head, "at": at}, open(LAST, "w"))
                # After the push, so a checkout lands on what this run just
                # published rather than trailing it by a run.
                refresh_checkouts()
                return held
        # A mirror that still refuses the push after every attempt is the one
        # case the hold cannot settle: another machine is pushing, and only a
        # later run reconciles against what it left.
        #
        # Reporting a push that did not happen is what lets the next run read this
        # machine's edits as the older side and pull the mirror back over them.
        print("config-sync: the mirror refused this run's push after %d attempts; "
              "this machine's edits are still only here" % ATTEMPTS, file=sys.stderr)
        return held


STATE = INTELLIGENCE / "state"
PUBLISHED = STATE / ".config-sync-published"
SCHEDULED = STATE / ".config-sync-scheduled"
LAST = STATE / ".config-sync-last"

# How long a publish waits for the edits behind it. A skill is edited in a run of
# calls rather than one, and a push per call would spend a round trip on each.
#
# The wait comes before the push rather than after, so the run's last edit is
# inside the push that follows it. Debouncing the other way round publishes the
# first edit of a burst and leaves the rest until something else fires.
QUIET_SECONDS = 20


def mark(path):
    STATE.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(time.time())))


def stamp(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def cmd_check():
    """Whether a cloud session starting now would load what this machine holds.

    A container installs from the mirror, so the mirror is the whole answer. The
    files are compared by content rather than by the manifest, which records what
    a sync intended rather than what a push achieved.
    """
    repo = ensure_repo()
    local, remote = {}, {}
    for base in SYNC_PATHS:
        local.update(walk(INTELLIGENCE, base))
        remote.update(walk(repo, base))
    held = [(rel, shape) for rel, path in sorted(local.items())
            if (shape := secret_shape(path))]
    blocked = {rel for rel, _ in held}
    behind = [rel for rel in sorted(set(local) | set(remote)) if rel not in blocked
              and not (rel in local and rel in remote and same(local[rel], remote[rel]))]
    if not (repo / CLOUD_SCRIPT).is_file():
        print("the mirror carries no %s, so a container has nothing to run" % CLOUD_SCRIPT)
        return 1
    if held:
        print("%d file(s) stay on this machine and never reach cloud:" % len(held))
        for rel, shape in held:
            print("  %s (%s)" % (rel, shape))
    if behind:
        print("%d file(s) differ between this machine and the mirror:" % len(behind))
        for rel in behind[:10]:
            print("  %s" % rel)
        if len(behind) > 10:
            print("  ... and %d more" % (len(behind) - 10))
        print("Run: config-sync.py publish")
        return 1
    if not held:
        print("in step: a cloud session would load what this machine holds")
    return 1 if held else 0


def cmd_hook_edit():
    """PostToolUse(Write|Edit): publish an edited skill, tool, or script.

    The session-start sync leaves a window the width of a session: a skill edited
    now and handed to cloud now arrives as the version before the edit. Publishing
    from the edit closes it, in the background so the edit does not wait on a push.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    target = (payload.get("tool_input") or {}).get("file_path") or ""
    if not target:
        return 0
    try:
        rel = Path(target).resolve().relative_to(INTELLIGENCE)
    except (ValueError, OSError):
        return 0
    if str(rel).split("/")[0] not in SYNC_PATHS:
        return 0
    # One publisher in flight at a time. It waits before it pushes, so an edit
    # made while one is pending is already inside the push it is waiting for.
    if int(time.time()) - stamp(SCHEDULED) < QUIET_SECONDS * 2:
        return 0
    mark(SCHEDULED)
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "publish-soon"],
                     start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def cmd_publish_soon():
    """Wait for the rest of the burst, then push it as one."""
    time.sleep(QUIET_SECONDS)
    try:
        sync()
    finally:
        drop(SCHEDULED)
    return 0




def remove(paths):
    """Retire paths: stamp them in the mirror, then delete them on both sides.

    Deleting a file and letting the sync notice does not work and cannot be made
    to: the next machine to sync holds a copy with a real edit time and the run
    restores it. The stamp is what a deletion needs to outrank that copy, so this
    is the only way a skill actually goes away everywhere.
    """
    repo = ensure_repo()
    removed = load_removals(repo)
    shas = load_json(repo / REMOVED_SHA)
    now = int(time.time())
    for raw in paths:
        rel = raw.strip("/")
        if not syncable(rel) and rel not in SYNC_PATHS:
            raise SystemExit("%s is not one of the synced paths" % rel)
        # Hash what is being retired before deleting it, so a byte-identical
        # copy surfacing later -- whatever its clock says -- reads as the
        # retired copy rather than as an edit.
        record = {}
        for root in (INTELLIGENCE, repo):
            target = root / rel
            if target.is_file():
                record.setdefault("", norm_sha(target))
            elif target.is_dir():
                for p in target.rglob("*"):
                    if p.is_file():
                        sub = str(p.relative_to(target)).replace(os.sep, "/")
                        record.setdefault(sub, norm_sha(p))
        record = {k: v for k, v in record.items() if v}
        if record:
            shas[rel] = record
        removed[rel] = now
        for root in (INTELLIGENCE, repo):
            target = root / rel
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                drop(target)
        print("retired %s" % rel)
    json.dump(removed, open(repo / REMOVED, "w"), indent=0, sort_keys=True)
    json.dump(shas, open(repo / REMOVED_SHA, "w"), indent=0, sort_keys=True)
    drop(repo / LEGACY_REMOVED)
    manifest = load_json(repo / MANIFEST)
    manifest = {k: v for k, v in manifest.items()
                if not retired(removed, k, int(v or 0))}
    json.dump(manifest, open(repo / MANIFEST, "w"), indent=0, sort_keys=True)
    if not publish(repo, "Retire %s" % ", ".join(p.strip("/") for p in paths)):
        raise SystemExit(
            "the mirror refused the retirement push, so the stamp is not recorded "
            "and the next sync on any machine will restore these paths. Re-run.")


def revive(paths):
    """Clear a path's retirement so a restored copy can live again.

    Content-aware retirement reads a byte-identical restore as the retired copy,
    which is right for a resurrection and wrong for a change of mind. This verb
    is the change of mind: it clears the stamp and the recorded hashes, and the
    next sync treats whichever copy exists as an ordinary file.
    """
    repo = ensure_repo()
    removed = load_json(repo / REMOVED)
    legacy = load_json(repo / LEGACY_REMOVED)
    shas = load_json(repo / REMOVED_SHA)
    hit = []
    for raw in paths:
        rel = raw.strip("/")
        found = False
        for ledger in (removed, legacy, shas):
            if rel in ledger:
                ledger.pop(rel)
                found = True
        if found:
            hit.append(rel)
            print("revived %s" % rel)
        else:
            print("%s carries no retirement stamp" % rel)
    if not hit:
        return
    json.dump(removed, open(repo / REMOVED, "w"), indent=0, sort_keys=True)
    json.dump(shas, open(repo / REMOVED_SHA, "w"), indent=0, sort_keys=True)
    if legacy:
        json.dump(legacy, open(repo / LEGACY_REMOVED, "w"), indent=0, sort_keys=True)
    else:
        drop(repo / LEGACY_REMOVED)
    if not publish(repo, "Revive %s" % ", ".join(hit)):
        raise SystemExit(
            "the mirror refused the revival push, so the stamp still stands "
            "and the next sync will delete these paths again. Re-run.")


USAGE = """Reconcile ~/.claude against the mirror, and retire what should go.

  config-sync.py [sync]          reconcile both copies, then push
  config-sync.py publish         full reconcile and push, skipping the fast path
  config-sync.py check           whether cloud would load what this machine holds
  config-sync.py remove <path>   stamp a path as retired and delete it on both sides
  config-sync.py revive <path>   clear a stamp so a restored copy can live again
"""


if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    # `check` answers a question, so a failure to reach the mirror is its answer
    # rather than something to swallow.
    if verb == "check":
        try:
            sys.exit(cmd_check())
        except Exception as e:
            print("config-sync: the mirror is not reachable (%s)" % e, file=sys.stderr)
            sys.exit(1)
    try:
        if verb in ("remove", "revive"):
            if len(sys.argv) < 3:
                raise SystemExit(USAGE)
            # Both verbs reconcile the clone and push it, which is the
            # window `sync` holds for the same reason.
            with sync_hold():
                (remove if verb == "remove" else revive)(sys.argv[2:])
            sys.exit(0)
        if verb == "hook-edit":
            sys.exit(cmd_hook_edit())
        if verb == "publish-soon":
            sys.exit(cmd_publish_soon())
        if verb in ("", "sync"):
            sync()
            sys.exit(0)
        if verb == "publish":
            sync(force=True)
            sys.exit(0)
        raise SystemExit(USAGE)
    except SystemExit:
        raise
    except Exception as e:
        # Never let a sync failure break a session start; the next run retries.
        print("config-sync: skipped (%s)" % e, file=sys.stderr)
        sys.exit(0)
