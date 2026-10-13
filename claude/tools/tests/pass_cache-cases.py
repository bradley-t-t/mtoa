#!/usr/bin/env python3
"""Cases the carry-forward has to get right.

A stored answer is only worth having if it can never be wrong. Every case here
is one way an answer stops standing: the project moved, the rule moved, the code
that checked it moved, the answer reached past the repository, or enough time
passed that the machine around the project is no longer the one that was
measured. The saving is the easy half; refusing to carry is the half that has to
hold.

Two keys, because the two kinds of answer depend on different things. A swept
answer depends on the tool that swept it, so editing the tool retires it. An
answer given by hand does not, and retiring those on a tool edit would throw
away a run's most expensive work for a change that cannot touch it.

Every fixture lives in a TemporaryDirectory with the store pinned inside it
through INTELLIGENCE_STATE_DIR, so no case can reach the real pass state.

  pass_cache-cases.py        run them
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "pass_cache.py")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def load(state_dir, **env):
    """A fresh import of the tool under test, pinned to a throwaway state tree."""
    os.environ["INTELLIGENCE_STATE_DIR"] = str(state_dir)
    for key in ("INTELLIGENCE_PASS_NO_CACHE", "INTELLIGENCE_PASS_CACHE_TTL_DAYS"):
        os.environ.pop(key, None)
    os.environ.update({k: v for k, v in env.items()})
    spec = importlib.util.spec_from_file_location("pass_cache_under_test", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = {"id": "AAA-01", "rule": "The page declares a title.", "when": "always",
        "check": {"type": "title_present"}, "severity": "blocker"}

PROBE_GATE = {"id": "AAA-02", "rule": "The service answers.", "when": "always",
              "check": {"type": "probe"}, "severity": "blocker"}

SELF_GATE = {"id": "PRC-01", "rule": "The run was opened.", "when": "always",
             "check": {"type": "run_opened"}, "severity": "blocker"}


def fixture(tmp, body="<title>Home</title>"):
    project = Path(tmp) / "project"
    project.mkdir(exist_ok=True)
    (project / "index.html").write_text(body)
    (project / "about.html").write_text("<title>About</title>")
    gates = Path(tmp) / "gates"
    gates.mkdir(exist_ok=True)
    (gates / "10-head.json").write_text('{"group": "head", "gates": []}')
    return project, gates


def opened(mod, tmp, project, gates, tool=None):
    cache = mod.open_for("seo", [str(project)], gates, tool or str(TARGET),
                         network={"probe"}, self_settling={"run_opened"})
    cache.set_scope(sorted(str(p) for p in project.glob("*.html")))
    cache.set_question(kind="site", flags=[], url=None, scope=None)
    return cache


# ------------------------------------------------------------------ the saving

def case_unchanged_carries():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "every route sets a title", run_id="r1")
        first.save()
        again = opened(mod, tmp, project, gates)
        got = again.carry_gate(GATE)
        check("unchanged project carries the answer", bool(got), True)
        check("carries the status", got["status"], "pass")
        check("names the run that answered", got["run"], "r1")


def case_changed_source_does_not_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "every route sets a title", run_id="r1")
        first.save()
        (project / "index.html").write_text("<title>Home</title><p>more</p>")
        again = opened(mod, tmp, project, gates)
        check("an edited source retires the answer", again.carry_gate(GATE), None)


def case_new_file_does_not_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "every route sets a title", run_id="r1")
        first.save()
        (project / "contact.html").write_text("<title>Contact</title>")
        again = opened(mod, tmp, project, gates)
        check("a route nobody has judged retires the answer",
              again.carry_gate(GATE), None)


def case_edited_rule_does_not_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "every route sets a title", run_id="r1")
        first.save()
        moved = dict(GATE, rule="The page declares a title, and it is unique.")
        again = opened(mod, tmp, project, gates)
        check("a reworded rule retires the answer", again.carry_gate(moved), None)
        check("the gate beside it still carries", bool(again.carry_gate(GATE)), True)


def case_edited_tool_retires_swept_but_not_hand():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        tool = Path(tmp) / "seo-pass.py"
        tool.write_text("# the checking tool\n")
        first = opened(mod, tmp, project, gates, tool=str(tool))
        first.record_gate(GATE, "pass", "swept", run_id="r1")
        hand = dict(GATE, id="AAA-09")
        first.record_gate(hand, "pass", "read every route", hand=True, run_id="r1")
        first.save()
        tool.write_text("# the checking tool, with a checker rewritten\n")
        again = opened(mod, tmp, project, gates, tool=str(tool))
        check("an edited tool retires what it swept", again.carry_gate(GATE), None)
        check("an edited tool leaves a hand answer standing",
              bool(again.carry_gate(hand, hand=True)), True)


# ------------------------------------------------------------- what never runs

def case_outward_answers_never_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        cache.record_gate(PROBE_GATE, "pass", "the project answered", run_id="r1")
        cache.record_gate(SELF_GATE, "pass", "run opened", run_id="r1")
        cache.save()
        again = opened(mod, tmp, project, gates)
        check("a probe is never carried", again.carry_gate(PROBE_GATE), None)
        check("a gate about the run is never carried", again.carry_gate(SELF_GATE), None)


def case_declared_never_is_honoured():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        gate = dict(GATE, id="AAA-03", check={"type": "title_present", "cache": "never"})
        cache = opened(mod, tmp, project, gates)
        cache.record_gate(gate, "pass", "answered", run_id="r1")
        cache.save()
        check("a gate that declares it is never carried is not",
              opened(mod, tmp, project, gates).carry_gate(gate), None)


def case_unsettled_answers_never_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        for status in ("open", "fail", "fixed", "disputed"):
            cache.record_gate(GATE, status, "still owed", run_id="r1")
        cache.save()
        check("work still owed is never served from a record",
              opened(mod, tmp, project, gates).carry_gate(GATE), None)


def case_a_pass_is_retired_by_a_later_failure():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        cache.record_gate(GATE, "pass", "answered", run_id="r1")
        cache.record_gate(GATE, "fail", "no longer true", run_id="r2")
        cache.save()
        check("a later failure clears the stored pass",
              opened(mod, tmp, project, gates).carry_gate(GATE), None)


def case_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state", INTELLIGENCE_PASS_CACHE_TTL_DAYS="0")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        cache.record_gate(GATE, "pass", "answered", run_id="r1")
        cache.save()
        time.sleep(1.1)
        check("an answer older than the window is not carried",
              opened(mod, tmp, project, gates).carry_gate(GATE), None)


def case_switch_off():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        cache.record_gate(GATE, "pass", "answered", run_id="r1")
        cache.save()
        off = load(Path(tmp) / "state", INTELLIGENCE_PASS_NO_CACHE="1")
        check("the override carries nothing",
              opened(off, tmp, project, gates).carry_gate(GATE), None)


def case_a_different_question_does_not_carry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "answered", run_id="r1")
        first.save()
        other = mod.open_for("seo", [str(project)], gates, str(TARGET),
                             network={"probe"}, self_settling={"run_opened"})
        other.set_scope(sorted(str(p) for p in project.glob("*.html")))
        other.set_question(kind="site", flags=["international"], url=None, scope=None)
        check("a run declaring a different flag asks a different question",
              other.carry_gate(GATE), None)


# ------------------------------------------------------------------ file rulings

def case_file_rulings_carry_per_file():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        index, about = project / "index.html", project / "about.html"
        hashes = mod.hash_paths([str(index), str(about)])
        cache.record_file(str(index), hashes[str(index)], "clear", "read in full", "r1")
        cache.record_file(str(about), hashes[str(about)], "clear", "read in full", "r1")
        cache.save()
        about.write_text("<title>About</title><p>rewritten</p>")
        again = opened(mod, tmp, project, gates)
        fresh = mod.hash_paths([str(index), str(about)])
        check("an untouched file keeps its ruling",
              bool(again.carry_file(str(index), fresh[str(index)])), True)
        check("an edited file owes a fresh read",
              again.carry_file(str(about), fresh[str(about)]), None)


def case_file_ruling_retired_by_an_edited_registry():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        index = project / "index.html"
        digest = mod.hash_paths([str(index)])[str(index)]
        cache.record_file(str(index), digest, "clear", "read in full", "r1")
        cache.save()
        (gates / "10-head.json").write_text('{"group": "head", "gates": [{"id": "H-1"}]}')
        again = opened(mod, tmp, project, gates)
        check("a file judged against rules that moved owes a fresh read",
              again.carry_file(str(index), digest), None)


def case_a_tool_edit_leaves_file_rulings_alone():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        tool = Path(tmp) / "seo-pass.py"
        tool.write_text("# the checking tool\n")
        cache = opened(mod, tmp, project, gates, tool=str(tool))
        index = project / "index.html"
        digest = mod.hash_paths([str(index)])[str(index)]
        cache.record_file(str(index), digest, "clear", "read in full", "r1")
        cache.save()
        tool.write_text("# the checking tool, rewritten\n")
        again = opened(mod, tmp, project, gates, tool=str(tool))
        check("editing the tool does not send every file back to be read",
              bool(again.carry_file(str(index), digest)), True)


def case_missing_files_are_forgotten():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        cache = opened(mod, tmp, project, gates)
        index, about = project / "index.html", project / "about.html"
        hashes = mod.hash_paths([str(index), str(about)])
        for path in (index, about):
            cache.record_file(str(path), hashes[str(path)], "clear", "read", "r1")
        cache.forget_missing([str(index)])
        cache.save()
        again = opened(mod, tmp, project, gates)
        check("a file the project no longer has is dropped",
              str(about) in again.data["files"], False)


def case_summary_counts_each_thing_once():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "answered", run_id="r1")
        first.save()
        again = opened(mod, tmp, project, gates)
        again.carry_gate(GATE)
        again.carry_gate(GATE)
        check("a gate looked up twice is reported once",
              again.summary(), "1 gate(s) carried forward")


def case_targets_do_not_share_a_store():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        other = Path(tmp) / "other"
        other.mkdir()
        (other / "index.html").write_text("<title>Home</title>")
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "answered", run_id="r1")
        first.save()
        second = mod.open_for("seo", [str(other)], gates, str(TARGET),
                              network={"probe"}, self_settling={"run_opened"})
        second.set_scope([str(other / "index.html")])
        second.set_question(kind="site", flags=[], url=None, scope=None)
        check("another project gets its own store", second.carry_gate(GATE), None)


def case_skills_do_not_share_a_store():
    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        project, gates = fixture(tmp)
        first = opened(mod, tmp, project, gates)
        first.record_gate(GATE, "pass", "answered", run_id="r1")
        first.save()
        other = mod.open_for("perf", [str(project)], gates, str(TARGET),
                             network={"probe"}, self_settling={"run_opened"})
        other.set_scope(sorted(str(p) for p in project.glob("*.html")))
        other.set_question(kind="site", flags=[], url=None, scope=None)
        check("another checklist gets its own store", other.carry_gate(GATE), None)


def case_a_sweep_in_a_new_worktree_finds_what_the_last_one_settled():
    """Answers belong to the repository, not to the worktree that swept it.

    Every sweep is cut a fresh worktree, so a bucket named after the target
    path was empty every time and a ruling filed under an absolute path was
    unreachable from the next. Both are keyed on the repository now.
    """
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        mod = load(Path(tmp) / "state")
        root = Path(tmp) / "repo"
        root.mkdir()

        def run(*args):
            subprocess.run(args, cwd=str(root), capture_output=True)

        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "T")
        (root / "a.txt").write_text("hello")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "one")
        tree = Path(tmp) / "wt"
        run("git", "worktree", "add", "-q", "-b", "sweep", str(tree))

        check("worktree resolves to its repository",
              mod.repo_of(tree), mod.repo_of(root))
        check("bucket survives a new worktree",
              mod.key_for("seo", [str(tree)]), mod.key_for("seo", [str(root)]))

        gates = Path(tmp) / "gates"
        gates.mkdir()
        (gates / "g.json").write_text('{"gates": []}')
        tool = Path(tmp) / "tool.py"
        tool.write_text("# tool\n")

        primary = mod.Cache("seo", [str(root)], gates, tool)
        digest = mod.hash_file(root / "a.txt")
        primary.record_file(root / "a.txt", digest, "clear", "read it", "run-1")
        primary.save()

        second = mod.Cache("seo", [str(tree)], gates, tool)
        carried = second.carry_file(tree / "a.txt", digest)
        check("a ruling survives a new worktree", carried is not None, True)
        check("the carried ruling is the one filed",
              (carried or {}).get("note"), "read it")

        (tree / "a.txt").write_text("changed")
        moved = mod.hash_file(tree / "a.txt")
        check("edited content is not carried",
              second.carry_file(tree / "a.txt", moved), None)

        loose = Path(tmp) / "loose.txt"
        loose.write_text("x")
        check("a path outside a repository buckets on itself",
              mod.repo_of(loose), loose.resolve())


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main():
    for case in CASES:
        try:
            case()
        except Exception as exc:
            FAILURES.append("%s raised %s: %s" % (case.__name__, type(exc).__name__, exc))
    if FAILURES:
        print("pass_cache: %d case(s) failed\n" % len(FAILURES))
        for line in FAILURES:
            print("  " + line)
        return 1
    print("pass_cache: %d case(s) pass" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
