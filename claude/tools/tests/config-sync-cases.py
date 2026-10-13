#!/usr/bin/env python3
"""Cases the config sync has to get right when two copies of a file disagree.

The sync settles a conflict by edit time, which is right whenever both copies
come from builds that differ only in age. It is wrong when one of them is an
older build carrying a later clock: time hands that copy the round and the newer
work is reverted by a run that reports itself as a push. A file with cases
written for it is judged by those first, and these fix that ordering.

That ordering has one exception, and the fault it prevents has the same shape as
the one above: a tool part-way through a multi-step edit fails its own cases as
well, and the publish the edit itself schedules reads the half-written copy as
the failing build. So a local copy touched since the last completed sync is left
as it is and named in the run's output, and settles on a later run.

The other half is retirement. The sync is additive, so a deleted file is restored
from whichever side still holds it; only a stamp outranks that copy, and a copy
edited after its stamp outranks the stamp back. A stamp only does that work if
the run reading the copy reads the stamp too, so the ledger is read from both
file names it is written under and the later stamp for a path is the one held.

  config-sync-cases.py        run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "config-sync.py")

# A stub standing in for a real cases file: the copy under test passes when it
# carries the marker and fails when it does not.
STUB = '''import os, sys
target = os.environ.get("INTELLIGENCE_TEST_TARGET", "")
sys.exit(0 if "GOOD" in open(target).read() else 1)
'''

GOOD = "# GOOD\nvalue = 1\n"
BAD = "value = 1\n"


def load(path):
    spec = importlib.util.spec_from_file_location("candidate", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(root, body, cases=True):
    tools = root / "tools"
    (tools / "tests").mkdir(parents=True, exist_ok=True)
    (tools / "widget.py").write_text(body)
    if cases:
        (tools / "tests" / "widget-cases.py").write_text(STUB)
    return tools / "widget.py"


WIDGET = "tools/widget.py"

# The two clocks a fixture pair carries, and the three places the last completed
# sync can sit against them. A mark after both says neither copy has been edited
# on this machine since it synced; a mark between them says the local one has,
# which is the shape of a change still being written.
OLD, NEW = 1_000_000, 2_000_000
SYNCED_AFTER, SYNCED_BETWEEN, SYNCED_BEFORE = 2_500_000, 1_500_000, 500_000


def outcome(here, there, local_body, remote_body, skipped):
    """What one reconcile did to a pair of copies."""
    local_now, mirror_now = here.read_text(), there.read_text()
    if skipped:
        if skipped != [WIDGET]:
            return "skipped %s" % skipped
        if (local_now, mirror_now) != (local_body, remote_body):
            return "skipped, then wrote local=%r mirror=%r" % (local_now, mirror_now)
        return "kept local, reported skipped"
    if local_now == mirror_now == local_body:
        return "pushed"
    if local_now == mirror_now == remote_body:
        return "pulled"
    return "local=%r mirror=%r" % (local_now, mirror_now)


def tiebreak(local_body, remote_body, local_newer, synced_at, cases=True,
             settles_to=None):
    """Reconcile one file against a recorded last-sync mark, and report the round.

    `settles_to` is a second local body written between two reconciles, which is
    the shape of an edit that finishes: the run after a skip finds a copy that
    answers its cases and arbitrates it like any other file.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        # Redirect whichever name the copy under test reads its own tree from, and
        # refuse to run when none of them is there. Setting a name the tool no
        # longer has is not an error Python reports: it adds an attribute nobody
        # reads, the reconcile walks the real tree, and the fixtures below are
        # written into it. That is how `tools/widget.py` and its cases file came
        # to sit in a live config directory and get committed to the mirror.
        redirected = [name for name in ("INTELLIGENCE", "CLAUDE") if hasattr(mod, name)]
        if not redirected:
            raise AssertionError(
                "%s exposes no tree global to redirect; these cases would write "
                "into the real config directory" % TARGET)
        for name in redirected:
            setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        # The mark the arbitration measures an edit against, held in the fixture
        # rather than in this machine's state directory, so each case states the
        # sync it is judged against instead of inheriting whatever this machine
        # last did.
        mod.LAST = Path(tmp) / "last"
        json.dump({"head": "0" * 40, "at": synced_at}, open(mod.LAST, "w"))
        here = build(home, local_body, cases)
        there = build(mirror, remote_body, cases)
        # Every path the reconcile can write to has to sit inside this temp tree.
        # A run that reaches the real one leaves fixtures in it that the next run
        # picks up as though they were cases anybody wrote on purpose.
        for path in [home, mirror, here, there, mod.LAST] \
                + [getattr(mod, n) for n in redirected]:
            assert str(path).startswith(tmp), "escaped the temp tree: %s" % path
        os.utime(here, (NEW, NEW) if local_newer else (OLD, OLD))
        os.utime(there, (OLD, OLD) if local_newer else (NEW, NEW))
        manifest = {WIDGET: NEW if not local_newer else OLD}
        recorded = manifest[WIDGET]
        skipped = []
        mod.reconcile(mirror, manifest, {}, {}, [], {}, set(), skipped)
        clock = NEW if local_newer else OLD
        got = {"local": here.read_text(), "mirror": there.read_text(),
               "outcome": outcome(here, there, local_body, remote_body, skipped)}
        # A win is recorded in two places -- the manifest entry, and the clock a
        # pull stamps onto the copy it lands. Reading only the first cannot tell a
        # skip from a pull, because a pull writes no manifest entry either.
        got["record"] = ("nothing stamped"
                         if manifest.get(WIDGET) == recorded
                         and int(here.stat().st_mtime) == clock
                         else "manifest %s, local clock %s"
                         % (manifest.get(WIDGET), int(here.stat().st_mtime)))
        if settles_to is not None:
            here.write_text(settles_to)
            os.utime(here, (NEW, NEW))
            skipped = []
            mod.reconcile(mirror, manifest, {}, {}, [], {}, set(), skipped)
            # Both rounds, because the second one publishes a finished edit
            # whether or not the first one left that edit intact.
            first = ("skipped" if got["outcome"] == "kept local, reported skipped"
                     else got["outcome"])
            got["after"] = "%s, then %s" % (
                first, outcome(here, there, settles_to, remote_body, skipped))
        return got


def tiebreak_case(local_body, remote_body, local_newer, synced_at, key, **rest):
    got = tiebreak(local_body, remote_body, local_newer, synced_at, **rest)[key]
    return got, got


def run_case(local_body, remote_body, local_newer, cases=True):
    """Reconcile one file between two trees and report which copy survived."""
    got = tiebreak(local_body, remote_body, local_newer, SYNCED_AFTER, cases)
    return got["local"], got["mirror"]


GONE = "gone"


def run_retirement(stamp_at, edited_at, only_local=False):
    """Reconcile one stamped path and report whether either copy survived."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        here = build(home, GOOD, cases=False)
        os.utime(here, (edited_at, edited_at))
        there = None
        if not only_local:
            there = build(mirror, GOOD, cases=False)
            os.utime(there, (edited_at, edited_at))
        else:
            (mirror / "tools").mkdir(parents=True)
        mod.reconcile(mirror, {}, {}, {"tools/widget.py": stamp_at})
        survived = [p for p in (here, there) if p is not None and p.exists()]
        got = GONE if not survived else survived[0].read_text()
        return got, got


def removals_case(under_current, under_legacy):
    """Load a retirement ledger split across the two file names, and report it."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        mirror = Path(tmp) / "mirror"
        mirror.mkdir(parents=True)
        if under_current is not None:
            (mirror / mod.REMOVED).write_text(json.dumps(under_current))
        if under_legacy is not None:
            (mirror / mod.LEGACY_REMOVED).write_text(json.dumps(under_legacy))
        got = mod.load_removals(mirror)
        return got, got


# A key body at the length a real one has, with the header assembled at run
# time: the sync's gate reads the body so this file passes it, but the mirror's
# CI reads headers alone and must not find one in these source bytes.
KEY_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"
KEY_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"
KEY_FILE = ("#!/usr/bin/env bash\n" + KEY_HEAD + "\n"
            + ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAA\n" * 5)
            + KEY_TAIL + "\n")

# The header with a body too short to be a key, which is what a fixture or a
# pattern looking for one carries.
KEY_SHAPED = (KEY_HEAD + "\n"
              "b3BlbnNzaC1rZXktdjEA\n"
              + KEY_TAIL + "\n")

# The secret-shaped fixtures are assembled rather than written out, so this file
# describes the shapes without carrying one. A cases file for a credential gate
# that spelled its fixtures in full would be withheld by the gate it tests, and
# the tool would lose the cases that settle its own sync races.
NAMED = "CRYPTOFORT_MASTER" + "_KEY"
PASSWORD_URL = "https://admin:" + "hunter2" + "@db.example.com/x\n"

# The two shapes that sit where a password sits and are not one: a shell
# interpolation in a clone command, and a bracketed run inside a regex.
INTERPOLATED = 'git clone "https://x-access-token:${token}@github.com/o/r.git"\n'
REGEX_FRAGMENT = "URL = re.compile(r\"://[^\\s/:@]+:[^\\s/@]+@\")\n"

# settings.json as it really is: the sync's own token, in the env block and in a
# credential-helper URL. Neither may be read as a reason to withhold the file,
# because withholding it stops every machine syncing.
TOKEN_FILE = ('{"env": {"GH_TOKEN": "ghp_exampleexampleexample",\n'
              '"GITHUB_TOKEN": "github_pat_example"}}\n'
              "https://octo-user:ghp_exampleexampleexample@github.com\n")


def shape_case(body):
    """What the gate makes of one file's contents."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "thing"
        path.write_text(body)
        got = mod.secret_shape(path) or "clean"
        return got, got


def withheld_case():
    """Reconcile a local file carrying a key, and report what the mirror got."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        here = build(home, KEY_FILE, cases=False)
        (mirror / "tools").mkdir(parents=True)
        held = []
        mod.reconcile(mirror, {}, {}, {}, held)
        reached = (mirror / "tools" / "widget.py").exists()
        # The file stays where it is, the mirror never sees it, and the run says
        # so. A silent withholding is the same defect as a silent push.
        got = "withheld" if not reached and held and here.exists() else "pushed"
        return got, got


def sha_of(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def sha_retirement_case(body):
    """A stamped path whose copy reappears with a clock past the stamp.

    The mtime rule alone reads that copy as an edit and restores it -- a
    checkout or a chmod is all it takes. With the retired content's hash on
    record, only bytes that actually differ count as the edit that revives.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        here = build(home, body, cases=False)
        os.utime(here, (2_000_000, 2_000_000))
        (mirror / "tools").mkdir(parents=True)
        mod.reconcile(mirror, {}, {}, {"tools/widget.py": 1_000_000}, [],
                      {"tools/widget.py": {"": sha_of(GOOD)}})
        got = GONE if not here.exists() else here.read_text()
        return got, got


def retired_copy_case(sidecar_holds):
    """What clearing the sidecar and stamp (the revive verb's work) changes."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "widget.py"
        path.write_text(GOOD)
        removed = {"tools/widget.py": 1_000_000} if sidecar_holds else {}
        shas = {"tools/widget.py": {"": sha_of(GOOD)}} if sidecar_holds else {}
        held = mod.retired_copy(removed, shas, "tools/widget.py", 2_000_000, path)
        got = "retired" if held else "live"
        return got, got


def trash_case():
    got = "ignored" if not load(TARGET).syncable("skills/.trash/x/SKILL.md") \
        else "synced"
    return got, got


def seen_case():
    """Reconcile reports every path either side holds, which is what lets the
    caller prune manifest entries recording files that exist nowhere."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        build(home, GOOD, cases=False)
        (mirror / "tools").mkdir(parents=True)
        seen = set()
        mod.reconcile(mirror, {}, {}, {}, [], {}, seen)
        ghost = {"tools/widget.py": 1, "tools/ghost.py": 1}
        kept = {k: v for k, v in ghost.items() if k in seen}
        got = "pruned" if list(kept) == ["tools/widget.py"] else "kept %s" % kept
        return got, got


def mode_case():
    """Content-equal copies still level up the executable bit."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        home, mirror = Path(tmp) / "local", Path(tmp) / "mirror"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        here = build(home, GOOD, cases=False)
        there = build(mirror, GOOD, cases=False)
        os.chmod(here, 0o755)
        os.chmod(there, 0o644)
        mod.reconcile(mirror, {}, {})
        got = "healed" if os.access(there, os.X_OK) else "still bare"
        return got, got


def scripts_cases_case():
    """scripts/<name>.py answers to a namespaced cases file, tools-style."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "tests").mkdir(parents=True)
        (root / "tools" / "tests" / "scripts-thing-cases.py").write_text("pass\n")
        hit = mod.cases_for(root, "scripts/thing.py")
        miss = mod.cases_for(root, "scripts/other.py")
        got = "mapped" if hit and not miss else "unmapped"
        return got, got


def git_quiet(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fast_path_case(touched_after):
    """With the mirror's head unchanged and nothing local touched since the
    recorded run, a sync gets to say so without walking or fetching anything."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        seedwork = root / "seedwork"
        git_quiet("clone", "-q", str(bare), str(seedwork), cwd=root)
        git_quiet("config", "user.email", "t@example.com", cwd=seedwork)
        git_quiet("config", "user.name", "T", cwd=seedwork)
        (seedwork / "seed").write_text("1\n")
        git_quiet("add", "-A", cwd=seedwork)
        git_quiet("commit", "-qm", "seed", cwd=seedwork)
        git_quiet("branch", "-M", "main", cwd=seedwork)
        git_quiet("push", "-q", "origin", "main", cwd=seedwork)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(seedwork),
                              capture_output=True, text=True).stdout.strip()

        home = root / "local"
        for name in ("INTELLIGENCE", "CLAUDE"):
            if hasattr(mod, name):
                setattr(mod, name, home)
        mod.SYNC_PATHS = ["tools"]
        mod.REMOTE = str(bare)
        mod.LAST = root / "last"
        here = build(home, GOOD, cases=False)
        stamp = 2_000_000
        for dirpath, _, filenames in os.walk(home):
            os.utime(dirpath, (1_000_000, 1_000_000))
            for name in filenames:
                os.utime(os.path.join(dirpath, name), (1_000_000, 1_000_000))
        if touched_after:
            os.utime(here, (3_000_000, 3_000_000))
        json.dump({"head": head, "at": stamp}, open(mod.LAST, "w"))
        got = "fast" if mod.in_step_fast() else "full"
        return got, got


def publish_case():
    """Push into a mirror that has moved since the run started, and report.

    A rejected push that reports success is how a machine's edits become the
    older side: the commit is dropped, the run says it pushed, and the next run
    pulls the mirror's copies back over the files this one just wrote.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        first, second = root / "first", root / "second"
        git_quiet("clone", "-q", str(bare), str(first), cwd=root)
        git_quiet("config", "user.email", "t@example.com", cwd=first)
        git_quiet("config", "user.name", "T", cwd=first)
        (first / "seed").write_text("1\n")
        git_quiet("add", "-A", cwd=first)
        git_quiet("commit", "-qm", "seed", cwd=first)
        git_quiet("branch", "-M", "main", cwd=first)
        git_quiet("push", "-q", "origin", "main", cwd=first)

        git_quiet("clone", "-q", str(bare), str(second), cwd=root)
        git_quiet("config", "user.email", "t@example.com", cwd=second)
        git_quiet("config", "user.name", "T", cwd=second)

        # The mirror moves while this run holds an older base.
        (first / "seed").write_text("2\n")
        git_quiet("commit", "-qam", "moved", cwd=first)
        git_quiet("push", "-q", "origin", "main", cwd=first)

        (second / "mine").write_text("mine\n")
        landed = mod.publish(second, "mine")
        got = "reported not landed" if landed is False else "reported landed"
        return got, got


# One run of the sync, driven against a fixture remote in a process of its own.
# The hold is between processes, so measuring it takes real ones. The tool's
# globals are redirected inside the child rather than in the parent, because
# each run needs its own tree, its own clone and its own marks file, and every
# path a run can write to has to land inside the temp root the parent made.
#
# The marks bracket the held region: ensure_repo is the first call inside the
# hold and publish the last, so two runs whose brackets do not overlap were
# ordered by the hold rather than by luck.
DRIVER = '''import importlib.util
import json
import sys
import time
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text())
root = config["root"]

spec = importlib.util.spec_from_file_location("candidate", config["target"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

for name in ("INTELLIGENCE", "CLAUDE"):
    if hasattr(mod, name):
        setattr(mod, name, Path(config["home"]))
mod.SYNC_PATHS = ["tools"]
mod.CACHE = Path(config["cache"])
mod.REMOTE = config["mirror"]
mod.STATE = Path(config["state"])
mod.PUBLISHED = mod.STATE / "published"
mod.SCHEDULED = mod.STATE / "scheduled"
mod.LAST = mod.STATE / "last"

tree = getattr(mod, "INTELLIGENCE", None) or getattr(mod, "CLAUDE")
for path in (tree, mod.CACHE, mod.STATE, mod.PUBLISHED, mod.SCHEDULED,
             mod.LAST, mod._state_root()):
    assert str(path).startswith(root), "escaped the temp tree: %s" % path

marks = []
real_ensure, real_publish = mod.ensure_repo, mod.publish


def ensure(*args, **kwargs):
    if not marks:
        marks.append(time.time())
        time.sleep(float(config["stall"]))
    return real_ensure(*args, **kwargs)


def published(*args, **kwargs):
    landed = real_publish(*args, **kwargs)
    marks.append(time.time())
    return landed


mod.ensure_repo, mod.publish = ensure, published
code = mod.cmd_check() if config["mode"] == "check" else (mod.sync(force=True), 0)[1]
Path(config["marks"]).write_text(json.dumps(
    {"begin": marks[0] if marks else 0, "end": marks[-1] if marks else 0}))
sys.exit(code)
'''

SEED = {"cloud/bootstrap.sh": "#!/bin/sh\n"}


def fixture_remote(root, extra=None):
    """A bare repo standing in for the mirror, seeded so a clone of it works.

    HEAD is pointed at main before anything is pushed, so a clone checks the
    branch out instead of landing on an unborn one. The cloud script is what
    `check` looks for before it will call a mirror usable.
    """
    bare = root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "--git-dir", str(bare), "symbolic-ref", "HEAD",
                    "refs/heads/main"], check=True)
    seed = root / "seed"
    git_quiet("clone", "-q", str(bare), str(seed), cwd=root)
    git_quiet("config", "user.email", "t@example.com", cwd=seed)
    git_quiet("config", "user.name", "T", cwd=seed)
    for rel, body in dict(SEED, **(extra or {})).items():
        path = seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git_quiet("add", "-A", cwd=seed)
    git_quiet("commit", "-qm", "seed", cwd=seed)
    git_quiet("branch", "-M", "main", cwd=seed)
    git_quiet("push", "-q", "origin", "main", cwd=seed)
    return bare


def build_named(root, stem, body):
    """One syncable file in a fixture tree, named so two runs cannot collide."""
    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    path = tools / ("%s.py" % stem)
    path.write_text(body)
    return path


def child_env(root, mod):
    """The environment a fixture run gets.

    The state directory is the fixture's, which is where the hold's file sits,
    so the runs contend with each other and with nothing outside the temp tree.
    The hold's variable is cleared because these cases are themselves run by a
    sync judging a copy of the tool, and a run that inherits the mark passes
    through the hold instead of taking it. The identity is spelled out so the
    fixture commits land on a machine that configures none.
    """
    env = dict(os.environ)
    env["INTELLIGENCE_STATE_DIR"] = str(root / "state")
    env.pop(getattr(mod, "LOCK_HELD", "INTELLIGENCE_SYNC_LOCK_HELD"), None)
    env.update({"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"})
    return env


def launch(root, mod, name, bare, home, mode="sync", stall=0.0):
    """Start one run against the fixture remote. Returns (process, marks file)."""
    driver = root / "drive.py"
    driver.write_text(DRIVER)
    config = root / ("%s.json" % name)
    marks = root / ("%s-marks.json" % name)
    config.write_text(json.dumps({
        "target": str(TARGET), "root": str(root), "home": str(home),
        "cache": str(root / ("%s-cache" % name)), "state": str(root / "state"),
        "mirror": str(bare), "marks": str(marks), "mode": mode, "stall": stall,
    }))
    proc = subprocess.Popen([sys.executable, str(driver), str(config)],
                            env=child_env(root, mod), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc, marks


def mirror_holds(root, bare, wanted):
    """Whether a fresh clone of the fixture carries each path with its content."""
    verify = root / "verify"
    git_quiet("clone", "-q", str(bare), str(verify), cwd=root)
    missing = [rel for rel, body in wanted.items()
               if not (verify / rel).is_file() or (verify / rel).read_text() != body]
    return missing


ALPHA, BETA = "# GOOD\nalpha = 1\n", "# GOOD\nbeta = 1\n"
_CONCURRENT = {}


def concurrent_result():
    """Two publishers racing for one mirror, each in a process of its own.

    Both carry a file the other does not, so the mirror can only end with both
    if each run's push survived the other's. The stall inside the held region
    makes an unordered pair overlap rather than miss each other by chance.
    """
    if _CONCURRENT:
        return _CONCURRENT
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = fixture_remote(root)
        first, second = root / "one-tree", root / "two-tree"
        build_named(first, "alpha", ALPHA)
        build_named(second, "beta", BETA)
        runs = [launch(root, mod, "one", bare, first, stall=0.5),
                launch(root, mod, "two", bare, second, stall=0.5)]
        codes = []
        for proc, _ in runs:
            try:
                proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            codes.append(proc.returncode)
        missing = mirror_holds(root, bare, {"tools/alpha.py": ALPHA,
                                            "tools/beta.py": BETA})
        windows = sorted((json.loads(m.read_text()) for _, m in runs
                          if m.is_file()), key=lambda w: w["begin"])
        _CONCURRENT["exits"] = ("both finished" if codes == [0, 0]
                                else "exits %s" % codes)
        _CONCURRENT["landed"] = ("both landed" if not missing
                                 else "missing %s" % missing)
        _CONCURRENT["order"] = "one at a time"
        if len(windows) != 2:
            _CONCURRENT["order"] = "recorded %d window(s)" % len(windows)
        elif windows[0]["end"] > windows[1]["begin"]:
            _CONCURRENT["order"] = "overlapped"
    return _CONCURRENT


def concurrent_case(key):
    got = concurrent_result()[key]
    return got, got


def reentrant_case():
    """A run inside the hold takes it again rather than waiting for itself.

    The wait is cut to a few seconds so a hold that blocked on itself shows up
    as an elapsed time instead of stalling the suite for the full one.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        os.environ.pop(mod.LOCK_HELD, None)
        began = time.monotonic()
        reached = False
        with mod.sync_hold(state, wait=3) as outer:
            with mod.sync_hold(state, wait=3) as inner:
                reached = True
        elapsed = time.monotonic() - began
        if reached and outer and not inner and elapsed < 2:
            got = "straight through"
        else:
            got = "outer=%s inner=%s reached=%s in %.1fs" % (
                outer, inner, reached, elapsed)
        return got, got


def single_run_case():
    """One run pushes what one run has always pushed, hold or no hold."""
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = fixture_remote(root)
        home = root / "one-tree"
        build_named(home, "alpha", ALPHA)
        proc, _ = launch(root, mod, "one", bare, home)
        out, err = proc.communicate(timeout=120)
        said = next((line for line in out.splitlines()
                     if line.startswith("config-sync:")), err.strip()[:60])
        missing = mirror_holds(root, bare, {"tools/alpha.py": ALPHA})
        got = "%s%s" % (said, "" if not missing else " missing %s" % missing)
        return got, got


_SKIPPED = {}


def skipped_run_result():
    """A whole run over a tool this machine is part-way through editing.

    The mirror holds a copy that answers its cases, the local tree holds one that
    does not yet, and the recorded sync sits an hour before the local edit. The
    run has to leave both copies as they are, name the path it left, and leave
    the mark where it was, so the run after this one still reads that edit as one
    in flight rather than as a build that arrived stale.
    """
    if _SKIPPED:
        return _SKIPPED
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = fixture_remote(root, {"tools/alpha.py": ALPHA,
                                     "tools/tests/alpha-cases.py": STUB})
        home = root / "one-tree"
        build_named(home, "alpha", BAD)
        (home / "tools" / "tests").mkdir(parents=True, exist_ok=True)
        (home / "tools" / "tests" / "alpha-cases.py").write_text(STUB)
        state = root / "state"
        state.mkdir(parents=True, exist_ok=True)
        before = int(time.time()) - 3600
        json.dump({"head": "0" * 40, "at": before}, open(state / "last", "w"))
        proc, _ = launch(root, mod, "one", bare, home)
        out, err = proc.communicate(timeout=120)
        said = next((line for line in out.splitlines() if "tools/alpha.py" in line), "")
        _SKIPPED["said"] = ("names the path and the reason" if "cases" in said
                            else "said %r" % (said or err.strip()[:60]))
        _SKIPPED["local"] = (home / "tools" / "alpha.py").read_text()
        _SKIPPED["mirror"] = ("untouched" if not mirror_holds(
            root, bare, {"tools/alpha.py": ALPHA}) else "overwritten")
        _SKIPPED["mark"] = ("held" if int(json.loads(
            (state / "last").read_text()).get("at") or 0) == before else "moved")
    return _SKIPPED


def skipped_run_case(key):
    got = skipped_run_result()[key]
    return got, got


def check_under_hold_case():
    """`check` answers a question, so it reads while a publisher holds the tree.

    The hold is taken here and the check runs inside it, against the same state
    directory, so a check that took the hold would sit there until this block
    ended -- which the timeout below reports as blocked rather than waiting out
    the full wait.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = fixture_remote(root, {"tools/alpha.py": ALPHA})
        home = root / "one-tree"
        build_named(home, "alpha", ALPHA)
        os.environ.pop(mod.LOCK_HELD, None)
        with mod.sync_hold(root / "state", wait=5) as outer:
            proc, _ = launch(root, mod, "check", bare, home, mode="check")
            try:
                out = proc.communicate(timeout=20)[0]
                answered = proc.returncode == 0 and "in step" in out
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                answered, out = False, "blocked"
        got = "answers while held" if outer and answered else "held up: %s" % out[:60]
        return got, got


def run_portable_hooks(command):
    """What a hook command in settings.json looks like after a sync reads it.

    settings.json travels to every machine and every cloud container, and a
    hook command naming one machine's home exits 127 on all the others. On a
    PreToolUse hook that reads as a refusal of every tool call, which is a
    routine hanging on an approval nobody is there to give.
    """
    mod = load(TARGET)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        commands = command if isinstance(command, list) else [command]
        (root / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": c}
                                       for c in commands]}]}}))
        mod.INTELLIGENCE = root
        mod.portable_hooks()
        body = json.loads((root / "settings.json").read_text())
    kept = [h["command"] for h in body["hooks"]["PreToolUse"][0]["hooks"]]
    got = kept[0] if len(kept) == 1 else "%d hooks: %r" % (len(kept), kept)
    return got, got


HOOK_RAW = "/Users/somebody/.tool/bin/tool hook PreToolUse"
HOOK_GUARDED = ('[ -e "$HOME/.tool/bin/tool" ] || exit 0; '
                'exec "$HOME/.tool/bin/tool" hook PreToolUse')
HOOK_TILDE = "~/.claude/tools/chrome-guard.py guard"
# A tool that installs its own row writes it guarded already, with the home it
# was installed under in both halves. The guard then answers for the machine
# that wrote it and stands the hook down everywhere else, so the row reads as
# wired on every machine and fires on one.
HOOK_GUARDED_HOME = ('[ -e /Users/somebody/.tool/bin/tool ] || exit 0; '
                     'exec /Users/somebody/.tool/bin/tool hook PreToolUse')
HOOK_NO_ARGS = "/Users/somebody/.tool/bin/tool"
HOOK_NO_ARGS_PORTABLE = ('[ -e "$HOME/.tool/bin/tool" ] || exit 0; '
                         'exec "$HOME/.tool/bin/tool"')


CASES = [
    ("a hook naming one machine's home is made portable",
     lambda: run_portable_hooks(HOOK_RAW), HOOK_GUARDED),
    ("a hook already portable is left alone",
     lambda: run_portable_hooks(HOOK_GUARDED), HOOK_GUARDED),
    ("a hook under a tilde is left alone",
     lambda: run_portable_hooks(HOOK_TILDE), HOOK_TILDE),
    ("a tool that rewires itself does not end up wired twice",
     lambda: run_portable_hooks([HOOK_GUARDED, HOOK_RAW]), HOOK_GUARDED),
    ("a hook guarded on one machine's home is made portable, guard and all",
     lambda: run_portable_hooks(HOOK_GUARDED_HOME), HOOK_GUARDED),
    ("a hook naming a home and taking no arguments keeps its shape",
     lambda: run_portable_hooks(HOOK_NO_ARGS), HOOK_NO_ARGS_PORTABLE),
    ("a stamped path is deleted on both sides",
     lambda: run_retirement(stamp_at=2_000_000, edited_at=1_000_000), GONE),
    ("a stamped path held on one side only still goes",
     lambda: run_retirement(stamp_at=2_000_000, edited_at=1_000_000, only_local=True),
     GONE),
    ("a copy edited after its stamp comes back",
     lambda: run_retirement(stamp_at=1_000_000, edited_at=2_000_000), GOOD),
    ("a copy that fails its cases loses to one that passes, whatever the clock says",
     lambda: run_case(GOOD, BAD, local_newer=False), GOOD),
    ("the same holds in the other direction",
     lambda: run_case(BAD, GOOD, local_newer=True), GOOD),
    ("with both copies passing, the later edit still wins",
     lambda: run_case(GOOD, GOOD + "extra = 2\n", local_newer=False),
     GOOD + "extra = 2\n"),
    ("a file with no cases written for it is settled by the clock alone",
     lambda: run_case(GOOD, BAD, local_newer=False, cases=False), BAD),
    ("a local copy edited since the last sync is left alone rather than reverted",
     lambda: tiebreak_case(BAD, GOOD, True, SYNCED_BETWEEN, "outcome"),
     "kept local, reported skipped"),
    ("a local copy no newer than the last sync still loses to a passing mirror",
     lambda: tiebreak_case(BAD, GOOD, True, SYNCED_AFTER, "outcome"), "pulled"),
    ("a mirror copy that fails while the local passes still loses",
     lambda: tiebreak_case(GOOD, BAD, False, SYNCED_BEFORE, "outcome"), "pushed"),
    ("a skipped path keeps its own clock and stays out of the manifest",
     lambda: tiebreak_case(BAD, GOOD, True, SYNCED_BETWEEN, "record"),
     "nothing stamped"),
    ("the run after a skip publishes the copy once its cases pass",
     lambda: tiebreak_case(BAD, GOOD, True, SYNCED_BETWEEN, "after",
                           settles_to=GOOD + "extra = 2\n"), "skipped, then pushed"),
    ("a file with no cases is settled by the clock however recently it was edited",
     lambda: tiebreak_case(BAD, GOOD, True, SYNCED_BETWEEN, "outcome", cases=False),
     "pushed"),
    ("a retirement filed under the second ledger name is still a retirement",
     lambda: removals_case({}, {"tools/x.py": 100}), {"tools/x.py": 100}),
    ("the later of the two stamps for one path is the one that holds",
     lambda: removals_case({"tools/x.py": 100}, {"tools/x.py": 200}),
     {"tools/x.py": 200}),
    ("a stamp restated under the current name outranks the older one",
     lambda: removals_case({"tools/x.py": 300}, {"tools/x.py": 200}),
     {"tools/x.py": 300}),
    ("a mirror carrying neither file retires nothing",
     lambda: removals_case(None, None), {}),
    ("a push into a mirror that has moved is reported as not landed",
     publish_case, "reported not landed"),
    ("a private key block keeps a file off the mirror",
     lambda: shape_case(KEY_FILE), "a private key block"),
    ("a named secret with a value keeps a file off the mirror",
     lambda: shape_case(NAMED + '="abc123"\n'), NAMED + " with a value"),
    ("a URL carrying a password keeps a file off the mirror",
     lambda: shape_case(PASSWORD_URL), "a URL carrying a password"),
    ("the sync's own token is not a reason to withhold settings.json",
     lambda: shape_case(TOKEN_FILE), "clean"),
    ("a named secret with no value is not a reason to withhold",
     lambda: shape_case("export CRYPTOFORT_MASTER_KEY=\n"), "clean"),
    ("a login token keeps a file off the mirror",
     lambda: shape_case("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abc123\n"),
     "CLAUDE_CODE_OAUTH_TOKEN with a value"),
    ("a login token named without a value is not a reason to withhold",
     lambda: shape_case("CLAUDE_CODE_OAUTH_TOKEN=${TOKEN}\n"), "clean"),
    ("ordinary source is clean",
     lambda: shape_case("def f():\n    return 1\n"), "clean"),
    ("a key header with no key under it is not a key",
     lambda: shape_case(KEY_SHAPED), "clean"),
    ("a token interpolated into a clone URL is not a password",
     lambda: shape_case(INTERPOLATED), "clean"),
    ("a regex that matches a URL is not a URL",
     lambda: shape_case(REGEX_FRAGMENT), "clean"),
    ("a withheld file stays local, never reaches the mirror, and is reported",
     withheld_case, "withheld"),
    ("a byte-identical copy with a clock past its stamp stays retired",
     lambda: sha_retirement_case(GOOD), GONE),
    ("a copy whose bytes actually differ is the edit that revives",
     lambda: sha_retirement_case(GOOD + "extra = 2\n"), GOOD + "extra = 2\n"),
    ("clearing the stamp and sidecar is what brings a path back",
     lambda: retired_copy_case(sidecar_holds=False), "live"),
    ("with the sidecar in place the same copy reads as retired",
     lambda: retired_copy_case(sidecar_holds=True), "retired"),
    ("the CLI's skill recycling never syncs",
     trash_case, "ignored"),
    ("a manifest entry for a file that exists nowhere is pruned",
     seen_case, "pruned"),
    ("content-equal copies still heal a dropped executable bit",
     mode_case, "healed"),
    ("a script answers to its namespaced cases file",
     scripts_cases_case, "mapped"),
    ("an untouched tree against an unmoved mirror syncs on the fast path",
     lambda: fast_path_case(touched_after=False), "fast"),
    ("anything touched since the last run forces the full walk",
     lambda: fast_path_case(touched_after=True), "full"),
    ("two runs publishing at once both finish",
     lambda: concurrent_case("exits"), "both finished"),
    ("neither run's file is missing from the mirror afterwards",
     lambda: concurrent_case("landed"), "both landed"),
    ("the second run reconciles after the first rather than alongside it",
     lambda: concurrent_case("order"), "one at a time"),
    ("a run inside the hold takes it again without waiting for itself",
     reentrant_case, "straight through"),
    ("one run on its own still pushes what it pushed before the hold",
     single_run_case, "config-sync: 1 pushed, 0 pulled"),
    ("check reads the mirror while another run holds the tree",
     check_under_hold_case, "answers while held"),
    ("a run over an edit in progress keeps the copy this machine is writing",
     lambda: skipped_run_case("local"), BAD),
    ("and leaves the mirror's copy where it is too",
     lambda: skipped_run_case("mirror"), "untouched"),
    ("and says which path it left, and why",
     lambda: skipped_run_case("said"), "names the path and the reason"),
    ("and leaves the mark, so the next run reads that edit as in flight",
     lambda: skipped_run_case("mark"), "held"),
]


def main():
    bad = 0
    for name, case, want in CASES:
        try:
            here, there = case()
            ok = here == want and there == want
        except Exception as err:                       # noqa: BLE001
            here = there = "raised %s" % err
            ok = False
        bad += not ok
        print("%s  %s" % ("pass" if ok else "FAIL", name))
        if not ok:
            print("    local=%r mirror=%r" % (here[:40], there[:40]))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
