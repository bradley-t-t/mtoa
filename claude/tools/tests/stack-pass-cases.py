#!/usr/bin/env python3
"""The cases the stack gate tool answers to.

The tool named in INTELLIGENCE_TEST_TARGET is the local shim; the engine it runs
sits inside the skill, and that is what these load. Nothing here touches the
network or a real service, so a case set that passes on one machine passes on
every machine.

What is covered is the part that would fail silently: whether every gate has a
checker behind it, whether the source checks read a synthetic project the way
they claim to, and whether each route out of a gate is actually closed - above
all the one this checklist adds, which is that a gate answering to a request
cannot be answered by hand.
"""

import contextlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import pathlib
from pathlib import Path

FAILURES = []

# The agency bar is the owner's, named in the owner's profile. The cases that
# read one carry a profile of their own, the way a machine's profile would, so
# no case depends on the machine it runs on.
AGENCY = {"agency": {
    "name": "Acme", "domain": "example.com", "url": "https://example.com",
    "bar": {"name": "Acme bar", "marker": "data-acme-bar",
            "packages": ["@acme/split-rail", "@acme/status-bar"],
            "components": ["AcmeBar", "SplitRail"], "accent_var": "--tu-accent"}}}


@contextlib.contextmanager
def profile(data):
    """MTOA_CONFIG pointed at a profile holding `data`, for the length of the block."""
    saved = os.environ.get("MTOA_CONFIG")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mtoa.json"
        path.write_text(json.dumps(data))
        os.environ["MTOA_CONFIG"] = str(path)
        try:
            yield path
        finally:
            if saved is None:
                os.environ.pop("MTOA_CONFIG", None)
            else:
                os.environ["MTOA_CONFIG"] = saved


def check(name, condition, detail=""):
    if condition:
        return
    FAILURES.append("%s%s" % (name, (" - " + detail) if detail else ""))


def load_engine():
    target = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                  or Path.home() / ".claude/tools/stack-pass.py")
    candidates = [
        Path.home() / ".claude/checklists/stack/bin/stack-pass.py",
        target.resolve().parent.parent / "checklists/stack/bin/stack-pass.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("stack_pass_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def fake_project(root, **files):
    """A project on disk, small enough to reason about and real enough to sweep."""
    root = Path(root)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def run_of(engine, root, **extra):
    run = {"id": "cases", "opened": "now", "session": "cases", "kind": "site",
           "targets": [str(root)], "repo": str(root), "url": "", "title": "cases",
           "flags": [], "results": {}, "touched": [], "probe": {}, "delegated": []}
    run.update(extra)
    return run


def every_gate_has_a_checker(engine):
    groups = engine.load_gates()
    gates = engine.all_gates(groups)
    check("gates load", len(gates) > 50, "only %d gates found" % len(gates))
    unknown = sorted({g["check"]["type"] for g in gates} - set(engine.CHECKS))
    check("every check type is dispatched", not unknown, "no checker for %s" % unknown)
    ids = [g["id"] for g in gates]
    check("gate ids are unique", len(ids) == len(set(ids)),
          "duplicates: %s" % sorted({i for i in ids if ids.count(i) > 1}))
    for gate in gates:
        check("%s has a rule" % gate["id"], len(gate.get("rule", "")) > 40)
        check("%s has a fix" % gate["id"], bool(gate.get("fix")))
    probes = {g["check"].get("probe") for g in gates if g["check"]["type"] == "probe"}
    check("every probe gate names a probe", all(probes), "a probe gate has no probe name")


def source_checks_read_the_project(engine):
    with tempfile.TemporaryDirectory() as tmp:
        root = fake_project(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "1.2.3",
                                           "dependencies": {"react": "18.0.0",
                                                            "unused-thing": "1.0.0"},
                                           "scripts": {"lint": "eslint ."}}),
               "src/app.jsx": "import 'react'\nconst k = import.meta.env.VITE_API_SECRET\n",
               ".env.example": "VITE_API_SECRET=\n",
               "package-lock.json": "{}"})
        run = run_of(engine, root)
        project = engine.build_project(run)
        ctx = engine.build_context(run, project)
        check("node is detected", ctx["node"])
        check("the framework is detected", ctx["framework"])
        check("env references are found", "VITE_API_SECRET" in project["env_names"],
              "found %s" % project["env_names"])

        status, detail = engine.check_calver({"check": {}}, project, run, ctx)
        check("a semantic version fails CalVer", status == engine.FAIL, detail)

        status, detail = engine.check_public_prefix_secrets({"check": {}}, project, run, ctx)
        check("a secret behind VITE_ is caught", status == engine.FAIL, detail)

        status, detail = engine.check_unused_deps({"check": {}}, project, run, ctx)
        check("an unused dependency is caught", status == engine.FAIL, detail)
        check("the used one is not named", "react" not in detail.split(":")[-1], detail)

        status, detail = engine.check_npm_script({"check": {"script": "format:check"}},
                                                 project, run, ctx)
        check("a missing script is caught", status == engine.FAIL, detail)

        status, detail = engine.check_footer_present({"check": {}}, project, run, ctx)
        check("an absent bar is caught", status == engine.FAIL, detail)

        # The same project with the bar in a shell passes, so the check is reading the
        # tree rather than always failing.
        (root / "src" / "App.jsx").write_text(
            "export default () => <footer data-acme-bar='split-rail' />\n")
        project = engine.build_project(run)
        status, detail = engine.check_footer_present({"check": {}}, project, run, ctx)
        check("a bar in the shell passes", status == engine.PASS, detail)


def the_bar_takes_its_tones_from_the_page(engine):
    """CHR-05 and CHR-07: the bar matches the site it lands on, or it is caught.

    A system colour tracks the browser's colour-scheme preference rather than the
    page, so a bar built on one is white under a dark site whenever the visitor's
    system is light. That is the shape these two gates exist to refuse.
    """
    with tempfile.TemporaryDirectory() as tmp:
        contract = ("export default () => (\n"
                    "  <footer data-acme-bar='split-rail' style={{\n"
                    "    colorScheme: 'inherit',\n"
                    "    background: 'transparent',\n"
                    "    color: 'color-mix(in oklab, currentColor 92%, transparent)',\n"
                    "    borderTop: '1px solid color-mix(in oklab, currentColor 13%, transparent)',\n"
                    "  }} />\n)\n")
        root = fake_project(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4"}),
               "src/App.jsx": contract})
        run = run_of(engine, root)
        project = engine.build_project(run)
        ctx = engine.build_context(run, project)

        status, detail = engine.check_footer_no_literals({"check": {}}, project, run, ctx)
        check("a bar mixed from the inherited ink passes", status == engine.PASS, detail)
        status, detail = engine.check_footer_ground({"check": {}}, project, run, ctx)
        check("a transparent ground passes", status == engine.PASS, detail)

        (root / "src" / "App.jsx").write_text(
            contract.replace("background: 'transparent'", "background: 'Canvas'"))
        project = engine.build_project(run)
        status, detail = engine.check_footer_ground({"check": {}}, project, run, ctx)
        check("a system colour is caught", status == engine.FAIL, detail)
        check("the refusal names the preference it follows",
              "colour-scheme" in detail or "color-scheme" in detail, detail)

        (root / "src" / "App.jsx").write_text(
            contract.replace("background: 'transparent'", "background: '#0b0b0c'"))
        project = engine.build_project(run)
        status, detail = engine.check_footer_ground({"check": {}}, project, run, ctx)
        check("a ground the bar paints itself is caught", status == engine.FAIL, detail)

        (root / "src" / "App.jsx").write_text(
            contract.replace("currentColor 92%", "#1a1a1a")
                    .replace("currentColor 13%", "white"))
        project = engine.build_project(run)
        status, detail = engine.check_footer_no_literals({"check": {}}, project, run, ctx)
        check("literal tones are caught", status == engine.FAIL, detail)

        # The one brand accent stays allowed, which is what keeps the gate narrow
        # enough to leave on.
        (root / "src" / "App.jsx").write_text(
            contract.replace("colorScheme: 'inherit',",
                             "colorScheme: 'inherit',\n    '--tu-accent': '#3b6ef5',"))
        project = engine.build_project(run)
        status, detail = engine.check_footer_no_literals({"check": {}}, project, run, ctx)
        check("the one retargeted accent still passes", status == engine.PASS, detail)


def a_probe_gate_refuses_a_hand_answer(engine):
    groups = engine.load_gates()
    index = engine.gate_index(groups)
    probe_gate = next(g for g in engine.all_gates(groups) if g["check"]["type"] == "probe")
    run = {"results": {probe_gate["id"]: {"status": "open", "auto": ""},
                       "VER-01": {"status": "open", "auto": "fail: 1.2.3 is not CalVer"},
                       "PRC-01": {"status": "open", "auto": ""}},
           "order": [probe_gate["id"], "VER-01", "PRC-01"]}
    note = "read the dashboard at 14:02 and the project is running"
    faults = engine._validate_answer(probe_gate["id"], "pass", note, run, index, {})
    check("a probe gate refuses pass", any("request" in f for f in faults), str(faults))
    faults = engine._validate_answer(probe_gate["id"], "na", note, run, index, {})
    check("a probe gate refuses na", any("request" in f for f in faults), str(faults))

    faults = engine._validate_answer("VER-01", "pass", "package.json line 3 says 2026.35.1",
                                     run, index, {"node": True})
    check("a swept-failing gate refuses pass", any("passing" in f for f in faults), str(faults))
    faults = engine._validate_answer("VER-01", "na", "package.json line 3 says 2026.35.1",
                                     run, index, {"node": True})
    check("a swept-failing gate refuses na", any("inapplicable" in f for f in faults), str(faults))
    faults = engine._validate_answer("VER-01", "disputed", "the check read package.json line 3 "
                                     "as 1.2.3; the file says 2026.35.1 and npm pkg get version "
                                     "agrees", run, index, {"node": True})
    check("a disputed answer with a measurement stands", not faults, str(faults))

    faults = engine._validate_answer("PRC-01", "pass", "the run was opened at 14:02 today",
                                     run, index, {})
    check("a bookkeeping gate refuses an attestation",
          any("run's own state" in f for f in faults), str(faults))

    # The sweep decides, so a status carrying no note at all stands. A note that
    # is there answers for itself: it names a path, a line, a measurement or a
    # quotation, or it is an assertion in the shape of a record.
    faults = engine._validate_answer("VER-01", "fixed", "", run, index, {"node": True})
    check("a status stands without prose beside it", not faults,
          str(faults))
    faults = engine._validate_answer("VER-01", "fixed", "done", run, index, {"node": True})
    check("a note naming nothing is refused",
          any("no evidence" in f for f in faults), str(faults))
    faults = engine._validate_answer("VER-01", "fixed",
                                     "package.json line 3 now reads 2026.35.1",
                                     run, index, {"node": True})
    check("a note naming a file and a value stands", not faults, str(faults))


def delegation_holds_the_run_open(engine):
    groups = engine.load_gates()
    index = engine.gate_index(groups)
    gate = index["PRC-05"]
    check("PRC-05 is the delegation gate", gate["check"]["type"] == "delegation")
    run = {"order": ["PRC-05"], "results": {"PRC-05": {"status": "open", "note": "", "auto": ""}},
           "files": {}, "probe": {}, "delegated": []}
    engine.settle_self(run, index, {})
    check("nothing recorded leaves it open", run["results"]["PRC-05"]["status"] == "open",
          run["results"]["PRC-05"]["note"])
    run["delegated"] = [{"to": "design", "note": "the bar sits 4px under the CTA at 375px"}]
    engine.settle_self(run, index, {})
    check("a handover settles it", run["results"]["PRC-05"]["status"] == "pass")
    run["delegated"] = []
    run["nothing_delegated"] = True
    engine.settle_self(run, index, {})
    check("an explicit nothing settles it", run["results"]["PRC-05"]["status"] == "pass")


def the_probe_never_writes_a_secret():
    # The copy that travels with the engine under test, so a bare CI checkout
    # answers for its own probe; the installed tree is the fallback.
    shim = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                or Path(__file__).resolve().parent.parent / "stack-pass.py")
    beside = shim.resolve().parent.parent / \
        "checklists" / "stack" / "scripts" / "probe.py"
    path = beside if beside.is_file() else \
        Path.home() / ".claude/checklists/stack/scripts/probe.py"
    if not path.is_file():
        FAILURES.append("probe.py is not installed beside the skill")
        return
    spec = importlib.util.spec_from_file_location("stack_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    for secret in ("sk_live_abcdef1234567890", "whsec_abcdef1234567890",  # gitleaks:allow (made-up keys)
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.aaaaaaaaaaaa",  # gitleaks:allow (made-up keys)
                   "ghp_abcdefghijklmnopqrstuvwxyz0123456789"):  # gitleaks:allow (made-up keys)
        scrubbed = probe.scrub("the call used %s and failed" % secret)
        check("scrub removes %s..." % secret[:8], secret not in scrubbed, scrubbed)
    probes = probe.Probes()
    probes.need("x.y", "SOME_TOKEN", "nothing was checked")
    check("a missing credential files as failing", probes["x.y"]["ok"] is False)
    check("a missing credential names the variable", "SOME_TOKEN" in probes["x.y"]["detail"])


def a_browser_gate_answers_unmeasured(engine):
    """LIV-02 reads a console, which needs something to render the page.

    Every other probe reaches its service over the network with a credential, so a
    failure there is configuration and the fix is the credential. A console reading
    is different in kind: no credential produces one. `unmeasured` covers that one
    case, and these hold it to that - the marker has to be recorded, and a probe
    that merely went unconfigured cannot borrow the route.
    """
    console = {"id": "LIV-02", "title": "The Console Is Clean",
               "check": {"type": "probe", "probe": "live.console"}, "when": "always"}
    vercel = {"id": "VRC-01", "title": "The Deployment Is Current",
              "check": {"type": "probe", "probe": "vercel.current"}, "when": "always"}
    index = {"LIV-02": console, "VRC-01": vercel}
    reason = "no rendering engine is available on this machine, so no page can be loaded"
    note = ("probe.py live.console needs a rendered page to read a console from and no "
            "engine is available here, so the reading was never taken")

    def run_with(**kw):
        base = {"results": {"LIV-02": {"status": "open", "auto": "fail: no browser"},
                            "VRC-01": {"status": "open", "auto": "fail: VERCEL_TOKEN is not set"}}}
        base.update(kw)
        return base

    marked = run_with(uninstrumented={"reason": reason})
    check("the console gate answers unmeasured on an uninstrumented run",
          engine._validate_answer("LIV-02", "unmeasured", note, marked, index) == [],
          str(engine._validate_answer("LIV-02", "unmeasured", note, marked, index)))
    check("unmeasured is refused while the run carries no marker",
          engine._validate_answer("LIV-02", "unmeasured", note, run_with(), index) != [])
    check("a probe that went unconfigured cannot borrow the unmeasured route",
          engine._validate_answer("VRC-01", "unmeasured", note, marked, index) != [])
    check("unmeasured takes a reason, not a shrug",
          engine._validate_answer("LIV-02", "unmeasured", "no browser", marked, index) != [])
    check("a probe gate still refuses an attestation of passing",
          engine._validate_answer("LIV-02", "pass", note, marked, index) != [])
    check("a probe gate still refuses not-applicable",
          engine._validate_answer("LIV-02", "na", note, marked, index) != [])
    check("the browser-probe set names only gates a browser has to read",
          engine.BROWSER_PROBES == {"LIV-02", "OBS-05", "CHR-08"},
          str(sorted(engine.BROWSER_PROBES)))



def _reading(rail_ground, footer_ground, **over):
    """A rail capture in the shape railProbe.scan() returns."""
    rail = {"found": True, "insideFooter": rail_ground == footer_ground,
            "paintsOwnGround": False, "ownBackground": "rgba(0, 0, 0, 0)",
            "ink": "#fbfbf8", "ground": rail_ground, "groundFrom": "div",
            "contrast": 18.49, "linkContrast": 7.43, "height": 40,
            "width": 1280, "fontSize": 11.0, "groups": 2,
            "count": 1, "signatures": 1}
    rail.update(over)
    return {"captured": "/", "viewport": "1280x900", "rail": rail,
            "footer": {"found": True, "ground": footer_ground, "ink": "#fbfbf8"}}


def the_rail_is_judged_against_its_footer(engine):
    """CHR-08: the defect that no amount of reading the source can show.

    The markup is identical whether the rail was mounted inside the footer or
    beside it, and every colour in it is correct either way. What separates the
    two is the ground the laid-out page put behind it, so the gate answers to a
    rendered reading and to nothing else.
    """
    gate = {"check": {"type": "rail_palette"}}
    check("every gate type in the registry has a checker",
          "rail_palette" in engine.CHECKS)

    status, detail = engine.check_rail_palette(gate, None, {}, None)
    check("with no reading filed the gate fails rather than passing",
          status == engine.FAIL, "%s: %s" % (status, detail))
    check("and the refusal names the probe that settles it",
          "rail-probe" in detail, detail)

    seated = {"rail": _reading("#0e0f12", "#0e0f12")}
    status, detail = engine.check_rail_palette(gate, None, seated, None)
    check("a rail on the footer's own ground passes",
          status == engine.PASS, "%s: %s" % (status, detail))

    # The shape the bug actually took: mounted at the app root, so the rail
    # inherited the light page body while the footer above it was dark.
    detached = {"rail": _reading("#fbfbf8", "#0e0f12", groundFrom="body")}
    status, detail = engine.check_rail_palette(gate, None, detached, None)
    check("a rail left on the page body under a dark footer fails",
          status == engine.FAIL, "%s: %s" % (status, detail))
    check("and the failure says which ground each of them is on",
          "#fbfbf8" in detail and "#0e0f12" in detail, detail)

    illegible = {"rail": _reading("#0e0f12", "#0e0f12", contrast=1.04, linkContrast=1.02)}
    status, _ = engine.check_rail_palette(gate, None, illegible, None)
    check("ink that cannot be read on its own ground fails",
          status == engine.FAIL)

    # One bar, mounted once, signing the page once.
    twice = {"rail": _reading("#0e0f12", "#0e0f12", count=2, signatures=2)}
    status, detail = engine.check_rail_palette(gate, None, twice, None)
    check("two rails on one page fails", status == engine.FAIL, detail)

    # The shape that actually shipped: a hand-rolled footer credit beside the
    # shared rail, carrying no rail marker of its own.
    copied = {"rail": _reading("#0e0f12", "#0e0f12", count=1, signatures=2)}
    status, detail = engine.check_rail_palette(gate, None, copied, None)
    check("a second hand-rolled credit beside the rail fails",
          status == engine.FAIL, detail)
    check("and the failure calls it a hand-rolled credit",
          "hand-rolled" in detail, detail)

    painted = {"rail": _reading("#0e0f12", "#0e0f12", paintsOwnGround=True,
                                ownBackground="rgb(255, 255, 255)")}
    status, _ = engine.check_rail_palette(gate, None, painted, None)
    check("a rail painting a ground of its own fails", status == engine.FAIL)

    # One bar on every site means one geometry on every site.
    for label, over in (("height", {"height": 96}),
                        ("type size", {"fontSize": 18.0}),
                        ("the two-part split", {"groups": 1})):
        status, _ = engine.check_rail_palette(gate, None, {"rail": _reading(
            "#0e0f12", "#0e0f12", **over)}, None)
        check("a rail that departs from the standard %s fails" % label,
              status == engine.FAIL)


def the_rail_gate_refuses_a_hand_answer(engine):
    """An attestation cannot tell a seated rail from a detached one."""
    gate = {"id": "CHR-08", "title": "The Rail Takes The Footer's Palette",
            "check": {"type": "rail_palette"}, "when": "pages"}
    index = {"CHR-08": gate}
    note = ("read the rendered rail against the footer on the home route and both "
            "sit on the same ground, #0e0f12")
    bare = {"results": {"CHR-08": {"status": "open", "auto": ""}}}
    check("CHR-08 refuses pass while no reading is filed",
          engine._validate_answer("CHR-08", "pass", note, bare, index) != [])
    check("CHR-08 refuses not-applicable while no reading is filed",
          engine._validate_answer("CHR-08", "na", note, bare, index) != [])
    filed = dict(bare, rail=_reading("#0e0f12", "#0e0f12"))
    check("once the reading is filed the gate can be answered",
          engine._validate_answer("CHR-08", "pass", note, filed, index) == [],
          str(engine._validate_answer("CHR-08", "pass", note, filed, index)))



def a_called_service_is_not_a_run_one(engine):
    """A third party's hostname must not read as this project's backend.

    The marker counted a bare supabase.co anywhere in the tree, and every site
    here loads its analytics from an edge function on one. A brochure site with
    no database weighed the condition true, opened the three service gates, and
    failed all three on a URL that was never going to be set.
    """
    marker = engine.SERVICE_MARKERS["supabase"]
    analytics = ('<script src="https://abcdefghijklmnopqrst.supabase.co'
                 '/functions/v1/analytics-tracker"></script>')

    check("marker: the analytics hostname alone does not count",
          not marker.search(analytics), "a called service read as a run one")
    check("marker: a preconnect to one does not count",
          not marker.search('<link rel="preconnect" href="https://x.supabase.co" />'))

    check("marker: the client library counts",
          bool(marker.search('import { createClient } from "@supabase/supabase-js"')))
    check("marker: a configured variable counts",
          bool(marker.search("VITE_SUPABASE_URL=https://x.supabase.co")))
    check("marker: a client construction counts",
          bool(marker.search("const db = createClient(url, key)")))
    check("marker: the CLI config counts",
          bool(marker.search("supabase/config.toml")))

    # A site that calls the tracker and owns a project still counts, because
    # the evidence that decides it is present either way.
    check("marker: both together still count",
          bool(marker.search(analytics + "\nSUPABASE_URL=https://y.supabase.co")))


def a_linked_repo_reads_as_linked(engine):
    """vercel link writes one of two files, and both mean linked.

    project.json is what a linked bare directory gets; repo.json is what a
    linked git repository gets, which is what every checkout here is. Reading
    only the first read a linked repo as linked to nothing, and the fix it
    proposed produced the file it was already ignoring.
    """
    import tempfile, json as _json
    gate = next(g for grp in engine.load_gates() for g in grp["gates"]
                if g["id"] == "SVC-30")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Neither file: genuinely unlinked.
        status, detail = engine.check_vercel_linked(gate, {"root": root}, None, {})
        check("link: an unlinked checkout fails", status == engine.FAIL, detail)

        # repo.json, the shape a linked git repository gets.
        (root / ".vercel").mkdir()
        (root / ".vercel" / "repo.json").write_text(_json.dumps({
            "remoteName": "origin",
            "projects": [{"id": "prj_x", "name": "site-com", "directory": ".",
                          "orgId": "team_y"}]}))
        status, detail = engine.check_vercel_linked(gate, {"root": root}, None, {})
        check("link: repo.json reads as linked", status == engine.PASS, detail)
        check("link: repo.json names the project", "site-com" in detail, detail)

        # project.json still wins where both exist.
        (root / ".vercel" / "project.json").write_text(_json.dumps({
            "projectId": "prj_z", "orgId": "team_y", "projectName": "direct-com"}))
        status, detail = engine.check_vercel_linked(gate, {"root": root}, None, {})
        check("link: project.json still reads as linked", status == engine.PASS, detail)
        check("link: project.json is preferred", "direct-com" in detail, detail)


def a_layout_component_counts_as_the_shell(engine):
    """CHR-01: a shell named for the surface it wraps is still a shell.

    React projects put one under layout/ and call it MarketingPage or
    DashboardShell. Matching only the filename reports a project that mounts the
    bar in exactly one place as one that scatters it per page, which is a fail no
    amount of correct work can clear.
    """
    with tempfile.TemporaryDirectory() as tmp:
        shell = ("import SplitRail from '@acme/split-rail'\n"
                 "export default ({ children }) => (\n"
                 "  <div><main>{children}</main><footer><SplitRail /></footer></div>\n)\n")
        root = fake_project(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4"}),
               "src/components/layout/MarketingPage.jsx": shell})
        run = run_of(engine, root)
        project = engine.build_project(run)
        ctx = engine.build_context(run, project)
        gate = {"id": "CHR-01"}
        status, note = engine.check_footer_present(gate, project, run, ctx)
        check("a layout component is the shell", status == engine.PASS,
              "CHR-01 said %s: %s" % (status, note))
    for path, want in (("src/components/layout/MarketingPage.jsx", True),
                       ("src/layouts/Root.tsx", True),
                       ("src/app/App.jsx", True),
                       ("src/pages/PaymentPage/PaymentPage.jsx", False),
                       ("src/components/nav/Navbar.jsx", False),
                       ("package.json", False)):
        got = bool(engine.SHELL_FILE.search(path))
        check("shell test on %s" % path, got == want,
              "wanted %s, got %s" % (want, got))


def naming_the_bar_is_not_rendering_it(engine):
    """CHR-05 and CHR-07: writing about the bar is not the bar.

    The marker matches any mention of the package, so a README documenting the
    dependency, a manifest depending on it and a workflow installing it all join
    the bar's own source. A version badge's hex colours then read as tones the bar
    named for itself, and the words "Canvas 2D" in a paragraph about a drawing
    surface read as the system colour that tracks the browser's preference. Both
    are findings about prose, and no edit to the bar can clear either.
    """
    with tempfile.TemporaryDirectory() as tmp:
        shell = ("import SplitRail from '@acme/split-rail'\n"
                 "export default ({ children }) => (\n"
                 "  <div><main>{children}</main><footer><SplitRail /></footer></div>\n)\n")
        readme = ("# Project\n\n"
                  "<img src='https://img.shields.io/badge/version-2026.35.6-f0a045' />\n"
                  "<img src='https://img.shields.io/badge/build-passing-07101c' />\n\n"
                  "| Site chrome | `@acme/split-rail` - the shared footer bar |\n\n"
                  "The tide chart is drawn on a Canvas 2D context, one frame per hour.\n")
        workflow = ("name: check\n"
                    "jobs:\n"
                    "  check:\n"
                    "    steps:\n"
                    "      - run: npm i @acme/split-rail\n"
                    "      - run: echo '::notice::#0b0b0c'\n")
        root = fake_project(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4",
                                           "dependencies": {"@acme/split-rail": "*"}}),
               "package-lock.json": json.dumps(
                   {"packages": {"node_modules/@acme/split-rail": {"version": "1.0.0"}}}),
               "src/App.jsx": shell,
               "docs/chrome.md": "SplitRail is mounted once, in the shell. #ffffff\n",
               ".github/workflows/ci.yml": workflow,
               "README.md": readme})
        run = run_of(engine, root)
        project = engine.build_project(run)
        ctx = engine.build_context(run, project)

        where = engine.bar_files(project)
        check("only the shell is the bar",
              where == ["src/App.jsx"],
              "bar files were %s" % where)

        # The two gates the prose reaches, asked against the same tree. A file
        # ledger that lets documentation through fails them on lines no edit to
        # the bar can move.
        status, detail = engine.check_footer_no_literals({"check": {}}, project, run, ctx)
        check("a badge colour is not a tone the bar named", status == engine.PASS, detail)
        status, detail = engine.check_footer_ground({"check": {}}, project, run, ctx)
        check("a paragraph naming a canvas is not a system colour",
              status == engine.PASS, detail)
        status, detail = engine.check_footer_present({"id": "CHR-01"}, project, run, ctx)
        check("the shell still carries the bar", status == engine.PASS, detail)
        status, detail = engine.check_footer_shared({"check": {}}, project, run, ctx)
        check("the shared bar still reads as a dependency", status == engine.PASS, detail)

    # A ledger naming what is not the bar leaks: prose arrives in whatever format
    # a project reaches for, and every one of these is swept as a source.
    for suffix, want in ((".jsx", True), (".tsx", True), (".js", True), (".mjs", True),
                         (".vue", True), (".svelte", True), (".astro", True),
                         (".html", True), (".css", True), (".scss", True),
                         (".md", False), (".mdx", False), (".txt", False),
                         (".json", False), (".yml", False), (".yaml", False),
                         (".toml", False), (".sql", False), (".sh", False),
                         (".rst", False), (".adoc", False), (".markdown", False)):
        got = suffix in engine.BAR_SOURCE
        check("a %s file counts as the bar" % suffix, got == want,
              "wanted %s, got %s" % (want, got))


def the_agency_bar_stands_down_without_a_profile(engine):
    """A profile naming no agency has no bar, so the bar's gates leave the run.

    The two chrome gates that are not about the bar stay, and no placeholder
    the agency's names would have filled reaches a gate's text unrendered.
    """
    with tempfile.TemporaryDirectory() as tmp, profile({}):
        root = fake_project(tmp, **{"package.json": json.dumps({"name": "x"}),
                                    "src/App.jsx": "export default () => <footer />\n"})
        run = run_of(engine, root)
        project = engine.build_project(run)
        ctx = engine.build_context(run, project)
        check("no profile names no bar", ctx["agency_bar"] is False)
        check("no profile owns no repository", not engine.owned("Acme/x"))
        groups = engine.load_gates()
        index = engine.gate_index(groups)
        for gid in ("CHR-01", "CHR-02", "CHR-03", "CHR-05", "CHR-07", "CHR-08",
                    "OBS-02", "OBS-03", "OBS-04", "OBS-05"):
            ok, why = engine.applicable(index[gid], run, ctx)
            check("%s stands down without a profile" % gid, not ok and "profile" in why, why)
        for gid in ("CHR-04", "CHR-06"):
            ok, why = engine.applicable(index[gid], run, ctx)
            check("%s applies without a profile" % gid, ok, why)
        left = re.findall(r"\{agency[a-z_]*\}", json.dumps(groups))
        check("no agency placeholder reaches a gate unrendered", not left, str(left))


def a_post_landing_gate_does_not_hold_the_landing(engine):
    """The two gates that answer to the state after a landing, and what they free.

    SVC-33 asks whether production serves the commit `main` points at, and
    OBS-05 whether a real error travelled the whole path. Neither can pass
    before the landing that would make it true, so a guard counting them holds
    the work until it has already shipped. PRC-06 then asks whether every
    applicable gate is answered, which those two cannot be, and the run cannot
    finish from either end.
    """
    check("POST_LANDING names the two gates that answer after a landing",
          getattr(engine, "POST_LANDING", None) == {"SVC-33", "OBS-05"},
          "got %r" % (getattr(engine, "POST_LANDING", None),))

    source = pathlib.Path(engine.__file__).read_text()
    check("a bookkeeping gate stands aside behind a post-landing gate",
          'not g.startswith("PRC-")' in source,
          "cmd_guard_land does not drop PRC- gates")


def all_is_not_a_narrowing(engine):
    """`--scope all` means the whole question, and has to reach the run as no scope."""
    flag = getattr(engine, "_scope_flag", None)
    check("_scope_flag exists", callable(flag))
    if not callable(flag):
        return
    check("all clears the scope", flag({"scope": ["all"]}) is None,
          "got %r" % (flag({"scope": ["all"]}),))
    check("no scope is no scope", flag({}) is None, "got %r" % (flag({}),))
    check("a real scope survives", flag({"scope": ["deps"]}) == "deps",
          "got %r" % (flag({"scope": ["deps"]}),))


def a_branch_on_develops_tip_owes_no_version(engine):
    """Nothing on the branch is nothing to release, so the version gate has no claim.

    Driven through the gate rather than read off the source. The engine's `git`
    is replaced for the length of the case, so the two commits can be made equal
    or different without a repository, and what is asserted is the verdict the
    gate returns rather than a phrase appearing somewhere in the file.
    """
    real_git = engine.git
    real_version = engine.project_version
    project = {"root": pathlib.Path("/nonexistent")}

    def drive(head, base, ours, theirs):
        def fake_git(root, *args, **kw):
            if args[:1] == ("rev-parse",):
                return (head if args[1] == "HEAD" else base) + "\n"
            if args[:1] == ("show",):
                return json.dumps({"version": theirs})
            return ""
        engine.git = fake_git
        engine.project_version = lambda _p: ours
        try:
            return engine.check_version_moved({}, project, {}, {})
        finally:
            engine.git = real_git
            engine.project_version = real_version

    status, detail = drive("abc123", "abc123", "2026.36.1", "2026.36.1")
    check("a branch on develop's tip passes", status == engine.PASS,
          "got %r - %s" % (status, detail))
    check("and says it carries no change", "carries no change" in detail,
          "got %r" % detail)

    status, detail = drive("aaa111", "bbb222", "2026.36.1", "2026.36.1")
    check("a branch with work and an unmoved version still fails",
          status == engine.FAIL, "got %r - %s" % (status, detail))

    status, _ = drive("aaa111", "bbb222", "2026.36.2", "2026.36.1")
    check("a branch that moved its version passes", status == engine.PASS)


def main():
    engine = load_engine()
    if engine is None:
        print("stack-pass: the skill is not installed, so its engine could not be loaded")
        return 1
    every_gate_has_a_checker(engine)
    with profile(AGENCY):
        source_checks_read_the_project(engine)
        the_bar_takes_its_tones_from_the_page(engine)
    a_probe_gate_refuses_a_hand_answer(engine)
    delegation_holds_the_run_open(engine)
    the_probe_never_writes_a_secret()
    a_browser_gate_answers_unmeasured(engine)
    the_rail_is_judged_against_its_footer(engine)
    the_rail_gate_refuses_a_hand_answer(engine)
    a_called_service_is_not_a_run_one(engine)
    a_linked_repo_reads_as_linked(engine)
    with profile(AGENCY):
        a_layout_component_counts_as_the_shell(engine)
        naming_the_bar_is_not_rendering_it(engine)
    the_agency_bar_stands_down_without_a_profile(engine)
    a_post_landing_gate_does_not_hold_the_landing(engine)
    all_is_not_a_narrowing(engine)
    a_branch_on_develops_tip_owes_no_version(engine)

    if FAILURES:
        print("%d case(s) failed:" % len(FAILURES))
        for row in FAILURES:
            print("  " + row)
        return 1
    print("stack-pass: every case passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
