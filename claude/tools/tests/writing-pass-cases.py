#!/usr/bin/env python3
"""Cases the writing pass has to get right.

The gate is the current text, re-read on every check, so both edges matter: a
named tell blocks a landing, and clean prose passes without a ledger to consult.
The guard only fires on a command that lands text, only in an owned repo, and an
allow retires one phrase in one file rather than the file. A detector that
cannot run blocks rather than waving the commit through, since no findings and
nothing checked look identical to every caller.

  writing-pass-cases.py        run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# The owners these cases name reach the tool through a profile of their own, the
# way a machine's profile would, so no case depends on the machine it runs on.
import json as _json
import tempfile as _tempfile
_PROFILE = Path(_tempfile.mkdtemp()) / "mtoa.json"
_PROFILE.write_text(_json.dumps({"github": {"owners": ["octo-user", "Acme"]}}))
os.environ["MTOA_CONFIG"] = str(_PROFILE)

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "writing-pass.py")

FAILURES = []

# Prose the detector scores clean, at the length the engine will score at all.
CLEAN = ("Fix the parser so a quoted path with spaces survives the split. "
         "The old code cut it at the first space.\n")

# Prose carrying exactly one lexical tell, so an allow for that one key is the
# whole difference between blocked and open.
ONE_TELL = ("The nightly job will delve through the archive and rebuild "
            "the index tables from scratch before anyone signs in.\n")

# Prose carrying several tells, for the cases where any finding is enough.
TELLS = ("This release is a real game-changer for the whole team. "
         "It weaves every request into a rich tapestry of features "
         "that will delve into what users actually need every day.\n")

OWNED = "octo-user/fixture"
FOREIGN = "someone-else/fixture"


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_repo(tmp, slug=OWNED, files=None):
    """A local repo whose origin URL names `slug`. The remote is never reached:
    the tool only reads the URL string to decide whether the repo is owned."""
    repo = Path(tmp) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "core.hooksPath", str(Path(tmp) / "no-hooks"))
    if slug:
        git(repo, "remote", "add", "origin", f"https://github.com/{slug}.git")
    for rel, body in (files or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        git(repo, "add", rel)
    return repo


def env_for(tmp, drop_node=False):
    """The child's environment: the allow ledger redirected into the temp tree,
    and optionally a PATH holding git but no node."""
    env = dict(os.environ)
    state = Path(tmp) / "state"
    assert str(state).startswith(str(tmp)), f"state escaped the temp tree: {state}"
    env["INTELLIGENCE_STATE_DIR"] = str(state)
    if drop_node:
        bins = Path(tmp) / "bin"
        bins.mkdir(exist_ok=True)
        (bins / "git").symlink_to("/usr/bin/git")
        env["PATH"] = str(bins)
    return env


def run_cli(tmp, args, cwd=None, stdin=None, drop_node=False):
    p = subprocess.run([sys.executable, str(TARGET), *args],
                       cwd=str(cwd) if cwd else tmp, input=stdin,
                       capture_output=True, text=True, env=env_for(tmp, drop_node))
    return p


def run_guard(tmp, command, cwd, drop_node=False):
    ev = json.dumps({"tool_name": "Bash",
                     "tool_input": {"command": command}, "cwd": str(cwd)})
    return run_cli(tmp, ["guard"], cwd=cwd, stdin=ev, drop_node=drop_node).returncode


CLEAN_MSG = "Fix the parser so a quoted path with spaces survives the split"
TELL_MSG = ("We delve into the tapestry of this release and call it a "
            "game-changer for everyone")


def guard_cases():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, files={"README.md": CLEAN})
        assert str(repo).startswith(tmp), f"repo escaped the temp tree: {repo}"
        check("guard: non-landing command",
              run_guard(tmp, "git status", repo), 0)
        check("guard: clean commit in a clean repo",
              run_guard(tmp, f'git commit -m "{CLEAN_MSG}"', repo), 0)
        check("guard: a message carrying tells is refused",
              run_guard(tmp, f'git commit -m "{TELL_MSG}"', repo), 2)
        check("guard: the combined -am spelling still carries the message",
              run_guard(tmp, f'git commit -am "{TELL_MSG}"', repo), 2)
        check("guard: a PR body carrying tells is refused",
              run_guard(tmp, f'gh pr create --title "Parser fix" --body "{TELL_MSG}"',
                        repo), 2)
        check("guard: a clean PR body passes",
              run_guard(tmp, f'gh pr create --title "Parser fix" --body "{CLEAN_MSG}"',
                        repo), 0)
        check("guard: an unrunnable detector blocks rather than waving through",
              run_guard(tmp, f'git commit -m "{CLEAN_MSG}"', repo, drop_node=True), 2)

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, files={"README.md": TELLS})
        check("guard: flagged tracked text blocks a clean commit",
              run_guard(tmp, f'git commit -m "{CLEAN_MSG}"', repo), 2)

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, slug=FOREIGN, files={"README.md": TELLS})
        check("guard: a repo outside the owned prefixes is left alone",
              run_guard(tmp, f'git commit -m "{TELL_MSG}"', repo), 0)

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, slug=None, files={"README.md": TELLS})
        check("guard: a repo with no origin is left alone",
              run_guard(tmp, f'git commit -m "{TELL_MSG}"', repo), 0)


def allow_cases():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, files={"README.md": ONE_TELL})
        check("check: the flagged file blocks",
              run_cli(tmp, ["check", "README.md"], cwd=repo).returncode, 1)
        check("allow: refused without a reason",
              run_cli(tmp, ["allow", "README.md", "tier1", "delve"],
                      cwd=repo).returncode, 1)
        done = run_cli(tmp, ["allow", "README.md", "tier1", "delve",
                             "--reason", "the job literally delves the archive"],
                       cwd=repo)
        check("allow: recorded with a reason", done.returncode, 0)
        ledger = Path(tmp) / "state" / "writing-pass"
        entries = list(ledger.glob("*.json")) if ledger.is_dir() else []
        check("allow: the ledger honors INTELLIGENCE_STATE_DIR",
              len(entries), 1)
        check("check: the retired phrase no longer blocks the file",
              run_cli(tmp, ["check", "README.md"], cwd=repo).returncode, 0)
        check("guard: a phrase ruled literal in the repo is literal in its "
              "messages too",
              run_guard(tmp, 'git commit -m "We delve through the archive '
                             'nightly and rebuild every index table"', repo), 0)
        shown = run_cli(tmp, ["allows"], cwd=repo)
        check("allows: names the retired key",
              "tier1|delve" in shown.stdout, True)
        check("allows: names the reason",
              "literally delves" in shown.stdout, True)
        listed = run_cli(tmp, ["list"], cwd=repo)
        check("list: an opened file drops off the listing",
              listed.stdout.strip(), "")


def verb_cases():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, files={"README.md": TELLS, "docs/notes.md": CLEAN})
        listed = run_cli(tmp, ["list"], cwd=repo)
        check("list: names only the blocking path",
              listed.stdout.strip(), "README.md")
        status = run_cli(tmp, ["status"], cwd=repo)
        check("status: exits clean while reporting", status.returncode, 0)
        check("status: counts the blocking file",
              "1 file(s) blocking:" in status.stdout, True)
        check("check: a clean tracked file passes",
              run_cli(tmp, ["check", "docs/notes.md"], cwd=repo).returncode, 0)

    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "plain"
        plain.mkdir()
        status = run_cli(tmp, ["status"], cwd=plain)
        check("status: stands down outside a repo", status.returncode, 0)
        check("status: says why it stood down",
              "does not apply" in status.stdout, True)
        check("check --stdin: tells exit 1",
              run_cli(tmp, ["check", "--stdin", "msg"], cwd=plain,
                      stdin=TELL_MSG + ".").returncode, 1)
        check("check --stdin: clean text exits 0",
              run_cli(tmp, ["check", "--stdin", "msg"], cwd=plain,
                      stdin=CLEAN).returncode, 0)
        check("unknown verb exits 1",
              run_cli(tmp, ["no-such-verb"], cwd=plain).returncode, 1)


def unit_cases():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["INTELLIGENCE_STATE_DIR"] = str(Path(tmp) / "state")
        try:
            mod = load()
        finally:
            del os.environ["INTELLIGENCE_STATE_DIR"]
        # The only tree the module can write is the ledger, and it has to sit
        # inside this temp dir or a case that records an allow would land it in
        # the live state directory.
        assert str(mod.STATE_DIR).startswith(tmp), \
            f"STATE_DIR escaped the temp tree: {mod.STATE_DIR}"

        check("candidate: documentation", mod.is_candidate("README.md"), True)
        check("candidate: mdx counts", mod.is_candidate("docs/guide.mdx"), True)
        check("candidate: a license is carried in, not written here",
              mod.is_candidate("LICENSE.md"), False)
        check("candidate: build output is excluded",
              mod.is_candidate("node_modules/pkg/README.md"), False)
        check("candidate: a skills tree is instruction, not copy",
              mod.is_candidate("skills/foo/notes.md"), False)
        check("candidate: a checklists tree is instruction, not copy",
              mod.is_candidate("checklists/design/references/polish.md"), False)
        check("candidate: copy-bearing config gates only when the path says so",
              mod.is_candidate("src/i18n/strings.json"), True)
        check("candidate: ordinary config stays out",
              mod.is_candidate("package.json"), False)
        check("candidate: a vendored subtree is upstream's prose",
              mod.is_candidate("lib/upstream/README.md",
                               vendored={Path("lib/upstream")}), False)

        base = "/base"
        got = mod.landing('git commit -m "msg here"', base)
        check("landing: commit message off -m",
              got, (Path(base), [("message", "msg here")]))
        got = mod.landing("cd sub && git commit -m msg", base)
        check("landing: cd moves the repo the guard checks",
              got and got[0], Path(base) / "sub")
        got = mod.landing("git -C /elsewhere commit -m msg", base)
        check("landing: -C moves it too", got and got[0], Path("/elsewhere"))
        check("landing: a push is not a landing",
              mod.landing("git push origin main", base), None)
        got = mod.landing('gh pr create --title "T" --body "B"', base)
        check("landing: PR title and body both collected",
              got and sorted(got[1]), [("body", "B"), ("title", "T")])
        msg = Path(tmp) / "msg.txt"
        msg.write_text("from a file")
        got = mod.landing(f"git commit -F {msg}", base)
        check("landing: -F pulls the message out of the file",
              got and got[1], [("message", "from a file")])

        check("blocking: a named tell gates",
              [i["type"] for i in mod.blocking_issues(
                  {"score": 5, "issues": [{"type": "tier1", "text": "delve"}]})],
              ["tier1"])
        check("blocking: an allow retires exactly that key",
              mod.blocking_issues(
                  {"score": 5, "issues": [{"type": "tier1", "text": "delve"}]},
                  allowed={"tier1|delve"}),
              [])
        check("blocking: rhythm measures never gate",
              mod.blocking_issues(
                  {"score": 5, "issues": [{"type": "em-dash-density",
                                           "text": "72 in 5763 words"}]}),
              [])
        check("blocking: a score over the ceiling gates on its own",
              [i["type"] for i in mod.blocking_issues(
                  {"score": mod.SCORE_CEILING + 1, "verdict": "x", "issues": []})],
              ["score"])
        check("blocking: a score at the ceiling does not",
              mod.blocking_issues(
                  {"score": mod.SCORE_CEILING, "verdict": "x", "issues": []}),
              [])
        check("blocking: an unreadable file reports rather than gates",
              mod.blocking_issues({"error": "unreadable", "score": 99}), [])


# The copywriting gate. Armed by the skill, fed by the edits that follow it, and
# answered at the stop by rescoring what it recorded. The marker holds paths and
# never a verdict, so the cases below always assert against the current text.

COPY_EVENT = {"session_id": "s1", "tool_input": {"skill": "copywriting"}}


def copy_event(session="s1", skill=None, path=None, transcript=None):
    ev = {"session_id": session}
    if skill is not None:
        ev["tool_input"] = {"skill": skill}
    if path is not None:
        ev["tool_input"] = {"file_path": str(path)}
    if transcript is not None:
        ev["transcript_path"] = str(transcript)
    return json.dumps(ev)


def marker_for(tmp, session="s1"):
    return Path(tmp) / "state" / "writing-pass" / ("copy-%s.json" % session)


def transcript_with(tmp, text, name="t.jsonl"):
    path = Path(tmp) / name
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }) + "\n")
    return path


def copy_gate_cases():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        check("gate: the copywriting skill arms it",
              marker_for(tmp).is_file(), True)
        check("gate: the marker records no verdict, only paths",
              sorted(json.loads(marker_for(tmp).read_text())), ["armed", "paths"])

    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="plugin:copywriting"))
        check("gate: a plugin-prefixed name arms the same gate",
              marker_for(tmp).is_file(), True)

    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="design"))
        check("gate: another skill does not arm it",
              marker_for(tmp).is_file(), False)

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.md"
        page.write_text(TELLS)
        run_cli(tmp, ["hook-edit"], stdin=copy_event(path=page))
        check("gate: an edit before the skill records nothing",
              marker_for(tmp).is_file(), False)
        check("gate: an unarmed stop passes",
              run_cli(tmp, ["guard-stop"], stdin=copy_event()).returncode, 0)

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.md"
        page.write_text(TELLS)
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        run_cli(tmp, ["hook-edit"], stdin=copy_event(path=page))
        check("gate: a written page is recorded",
              json.loads(marker_for(tmp).read_text())["paths"],
              [str(page.resolve())])
        check("gate: flagged copy refuses the stop",
              run_cli(tmp, ["guard-stop"], stdin=copy_event()).returncode, 2)
        check("gate: a refusal names the phrase",
              "delve" in run_cli(tmp, ["guard-stop"], stdin=copy_event()).stderr,
              True)
        check("gate: a refusal leaves the gate armed",
              marker_for(tmp).is_file(), True)

        # The gate opens on the text, not on an answer recorded about it.
        page.write_text(CLEAN)
        check("gate: rewriting the copy opens the gate",
              run_cli(tmp, ["guard-stop"], stdin=copy_event()).returncode, 0)
        check("gate: a clean stop disarms it",
              marker_for(tmp).is_file(), False)

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.md"
        page.write_text(TELLS)
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        run_cli(tmp, ["hook-edit"], stdin=copy_event(path=page))
        env = env_for(tmp)
        env["INTELLIGENCE_ALLOW_COPY"] = "1"
        p = subprocess.run([sys.executable, str(TARGET), "guard-stop"], cwd=tmp,
                           input=copy_event(), capture_output=True, text=True,
                           env=env)
        check("gate: the override opens it", p.returncode, 0)
        check("gate: the override disarms it", marker_for(tmp).is_file(), False)

    with tempfile.TemporaryDirectory() as tmp:
        vend = Path(tmp) / "vendor"
        vend.mkdir()
        (vend / "VENDOR.md").write_text("upstream\n")
        upstream = vend / "page.md"
        upstream.write_text(TELLS)
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        run_cli(tmp, ["hook-edit"], stdin=copy_event(path=upstream))
        check("gate: upstream text under a marker is not recorded",
              json.loads(marker_for(tmp).read_text())["paths"], [])
        check("gate: upstream text alone does not refuse",
              run_cli(tmp, ["guard-stop"], stdin=copy_event()).returncode, 0)

    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        flagged = transcript_with(tmp, TELLS)
        check("gate: copy handed back in a reply is scored too",
              run_cli(tmp, ["guard-stop"],
                      stdin=copy_event(transcript=flagged)).returncode, 2)

    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        clean = transcript_with(tmp, CLEAN)
        check("gate: a clean reply opens it",
              run_cli(tmp, ["guard-stop"],
                      stdin=copy_event(transcript=clean)).returncode, 0)

    with tempfile.TemporaryDirectory() as tmp:
        run_cli(tmp, ["hook-skill"], stdin=copy_event(skill="copywriting"))
        check("gate: an unreadable detector refuses rather than passing",
              run_cli(tmp, ["guard-stop"], stdin=copy_event(
                  transcript=transcript_with(tmp, TELLS)),
                  drop_node=True).returncode, 2)


def main():
    guard_cases()
    allow_cases()
    verb_cases()
    copy_gate_cases()
    unit_cases()
    for f in FAILURES:
        print(f"FAIL {f}")
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
