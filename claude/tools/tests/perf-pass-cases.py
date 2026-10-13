#!/usr/bin/env python3
"""The cases the performance gate tool answers to.

The tool named in INTELLIGENCE_TEST_TARGET is the local shim; the engine it runs
sits inside the skill, and that is what these load. Nothing here touches the
network, so a case set that passes on one machine passes on every machine.

What is covered is the part that would fail silently: whether every gate has a
checker behind it, whether the parity diff is clean on an unchanged page and
catches a real change, and whether each of the five routes out of a gate is
actually closed.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        return
    FAILURES.append("%s%s" % (name, (" - " + detail) if detail else ""))


def load_engine():
    """The engine the shim runs, imported under a name a module can hold."""
    target = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                  or Path.home() / ".claude/tools/perf-pass.py")
    candidates = [
        Path.home() / ".claude/checklists/perf/bin/perf-pass.py",
        target.resolve().parent.parent / "checklists/perf/bin/perf-pass.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("perf_pass_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def capture(route, width, elements, console=None, interactive=None, animations=None):
    return {"route": route, "viewport": {"w": width, "h": 844}, "built": True,
            "elements": elements, "console": console or [],
            "interactive": interactive or [], "animations": animations or [],
            "routes": [route]}


def element(sel, x, y, w, h, **style):
    base = {"color": "rgb(0, 0, 0)", "fontFamily": "Barlow", "fontSize": "16px",
            "boxShadow": "none", "opacity": "1"}
    base.update(style)
    return {"sel": sel, "x": x, "y": y, "w": w, "h": h, "style": base}


def run_cases(engine):
    groups = engine.load_gates()
    gates = engine.all_gates(groups)

    check("registry loads", len(gates) > 40, "found %d gates" % len(gates))

    # A gate whose check type has no implementation reports inconclusive forever,
    # which reads to anybody skimming a report exactly like a gate nobody reached.
    missing = sorted({g["check"]["type"] for g in gates
                      if g["check"]["type"] not in engine.CHECKS})
    check("every gate has a checker", not missing, "no implementation for %s" % missing)

    ids = [g["id"] for g in gates]
    check("gate ids are unique", len(ids) == len(set(ids)),
          "duplicated: %s" % [i for i in ids if ids.count(i) > 1])

    for gate in gates:
        check("%s names a fix" % gate["id"], bool(gate.get("fix") or gate["id"].startswith("PRC")))
        check("%s states a rule" % gate["id"], len(gate.get("rule", "")) > 40)

    # Parity is the invariant the whole checklist rests on. A diff that reports a
    # change on an unchanged page is noise nobody will keep reading; one that misses
    # a real change is worse, because the run closes saying the page is untouched.
    same = [element("h1\"Welcome\"", 0, 0, 300, 40),
            element("img[hero.webp]", 0, 48, 390, 260),
            element("a.btn\"Book\"", 16, 320, 120, 44)]
    run = {"parity": {"before": [capture("/", 390, same)],
                      "after": [capture("/", 390, [dict(e) for e in same])]}}
    moved, gone = engine.geometry_deltas(run)
    styles = engine.style_deltas(run)
    check("unchanged page diffs clean", not moved and not gone and not styles,
          "moved=%s gone=%s styles=%s" % (moved, gone, styles))

    shifted = [dict(e) for e in same]
    shifted[1] = dict(shifted[1], y=62)
    run = {"parity": {"before": [capture("/", 390, same)],
                      "after": [capture("/", 390, shifted)]}}
    moved, gone = engine.geometry_deltas(run)
    check("a 14px shift is caught", len(moved) == 1, "moved=%s" % moved)

    # Subpixel rounding differs between runs, so a strict compare would fail on noise.
    nudged = [dict(e) for e in same]
    nudged[1] = dict(nudged[1], y=48.4)
    run = {"parity": {"before": [capture("/", 390, same)],
                      "after": [capture("/", 390, nudged)]}}
    moved, _ = engine.geometry_deltas(run)
    check("subpixel noise is not a change", not moved, "moved=%s" % moved)

    restyled = [dict(e) for e in same]
    restyled[0] = dict(restyled[0], style=dict(restyled[0]["style"], fontFamily="system-ui"))
    run = {"parity": {"before": [capture("/", 390, same)],
                      "after": [capture("/", 390, restyled)]}}
    check("a font swap is caught", len(engine.style_deltas(run)) == 1)

    dropped = [dict(e) for e in same[:2]]
    run = {"parity": {"before": [capture("/", 390, same)],
                      "after": [capture("/", 390, dropped)]}}
    _, gone = engine.geometry_deltas(run)
    check("a vanished element is caught", len(gone) == 1, "gone=%s" % gone)

    # The five routes out of a gate, each of which turned a checklist into a survey.
    index = engine.gate_index(groups)
    swept = {"status": "open", "auto": "fail: three routes are statically imported",
             "note": ""}
    clean = {"status": "open", "auto": "pass: every route is dynamic", "note": ""}
    run = {"results": {"JSB-01": dict(swept), "PAR-02": dict(clean), "IMG-01": dict(swept)}}
    ctx = {"always": True, "js": True, "router": True, "image": True}

    faults = engine._validate_answer("JSB-01", "pass", "looks fine to me now", run, index, ctx)
    check("pass on a swept failure is refused", any("cannot be answered as passing" in f
                                                    for f in faults), str(faults))

    faults = engine._validate_answer("JSB-01", "na", "not relevant for this project", run, index, ctx)
    check("na on a swept failure is refused", any("present and unmet" in f for f in faults),
          str(faults))

    faults = engine._validate_answer("PAR-02", "pass", "checked the page, it looks identical",
                                     run, index, ctx)
    check("a hand answer on a parity gate is refused",
          any("answers to the captures" in f for f in faults), str(faults))

    faults = engine._validate_answer("PAR-02", "na", "parity does not apply to this change",
                                     run, index, ctx)
    check("na on a parity gate is refused",
          any("answers to the captures" in f for f in faults), str(faults))

    # What decides a gate is the sweep, so a bare status stands on a gate the sweep
    # did not fail. Prose beside it is a record and changes no verdict.
    faults = engine._validate_answer("IMG-01", "fixed", "done", run, index, ctx)
    check("a status stands without prose beside it", not faults, str(faults))

    faults = engine._validate_answer("IMG-01", "fixed", "added responsive widths everywhere",
                                     run, index, ctx)
    check("a longer note is equally acceptable", not faults,
          str(faults))

    faults = engine._validate_answer("IMG-01", "fixed",
                                     "srcset and sizes on all 6 images in GallerySection.jsx",
                                     run, index, ctx)
    check("an evidenced fix is accepted", not faults, str(faults))

    faults = engine._validate_answer("PAR-02", "disputed", "short", run, index, ctx)
    check("a dispute with no measurement is refused",
          any("takes the measurement" in f for f in faults), str(faults))

    # A scoped run that could drop parity would be a licence to move the page quietly.
    for scope in engine.SCOPES:
        par = [g for g in gates if g["id"].startswith("PAR-")]
        check("scope %s keeps parity" % scope,
              all(engine.in_scope(g, scope) for g in par))

    # A checker that raises must never read as a pass.
    with tempfile.TemporaryDirectory() as work:
        Path(work, "index.html").write_text("<html><body><div id=root></div></body></html>")
        run = engine.new_run([work], "cases", "spa")
        project = engine.build_project(run)
        context = engine.build_context(run, project)
        for gate in gates:
            fn = engine.CHECKS.get(gate["check"]["type"])
            if fn is None or gate["check"]["type"] in engine.SELF_SETTLING:
                continue
            try:
                status, detail = fn(gate, project, run, context)
            except Exception as exc:
                FAILURES.append("%s raised on an empty project: %s" % (gate["id"], exc))
                continue
            check("%s returns a known status" % gate["id"],
                  status in (engine.PASS, engine.FAIL, engine.UNKNOWN), str(status))
            check("%s explains itself" % gate["id"], isinstance(detail, str) and len(detail) > 5)


def unmeasured_cases(engine):
    """The route out of a parity gate on a machine that cannot render.

    Parity refuses `pass` and `na` because an attestation is exactly what the
    captures replace. That leaves a machine with no rendering engine unable to
    close a run at all, so `unmeasured` exists - and it is only worth having if it
    stays narrow. These cases hold the narrowness: the marker has to be recorded,
    the gate has to be a parity one, a run already holding a capture has nothing
    unmeasured about it, and the reason has to say what was missing.
    """
    gate = {"id": "PAR-02", "title": "Rendered Geometry Is Unchanged",
            "check": {"type": "parity_geometry"}, "when": "always"}
    index = {"PAR-02": gate, "JSB-01": {"id": "JSB-01", "title": "Bundle",
                                        "check": {"type": "bundle_size"}, "when": "always"}}
    reason = ("no rendering engine is available on this machine, so parity-probe.js "
              "cannot be run over the built page")
    note = ("parity-probe.js needs a rendered DOM to read boxes from and no engine is "
            "available here, so neither capture could be taken")

    def run_with(**kw):
        base = {"results": {"PAR-02": {"status": "open", "auto": "fail: no before capture"},
                            "JSB-01": {"status": "open", "auto": "fail: 240KB over budget"}}}
        base.update(kw)
        return base

    marked = run_with(uninstrumented={"reason": reason})
    check("a parity gate on an uninstrumented run answers unmeasured",
          engine._validate_answer("PAR-02", "unmeasured", note, marked, index) == [],
          str(engine._validate_answer("PAR-02", "unmeasured", note, marked, index)))

    check("unmeasured is refused while the run carries no marker",
          engine._validate_answer("PAR-02", "unmeasured", note, run_with(), index) != [])

    check("unmeasured is refused on a gate that is not parity",
          engine._validate_answer("JSB-01", "unmeasured", note, marked, index) != [])

    held = run_with(uninstrumented={"reason": reason},
                    parity={"before": [{"route": "/", "elements": []}]})
    check("a run holding a before capture has nothing unmeasured about it",
          engine._validate_answer("PAR-02", "unmeasured", note, held, index) != [])

    check("unmeasured takes a reason, not a shrug",
          engine._validate_answer("PAR-02", "unmeasured", "no browser", marked, index) != [])

    check("parity still refuses an attestation of passing",
          engine._validate_answer("PAR-02", "pass", note, marked, index) != [])

    check("parity still refuses not-applicable",
          engine._validate_answer("PAR-02", "na", note, marked, index) != [])


def parity_motion_cases(engine):
    # Canvas motion leaves no entry in getAnimations(), so a page built that way
    # records nothing on either side. Two empty sides settle the gate.
    frame = {"route": "/", "viewport": {"w": 1280}, "animations": [], "elements": []}
    run = {"parity": {
        "before": [dict(frame)],
        "after": [dict(frame)],
    }}
    verdict, detail = engine.CHECKS["parity_motion"]({}, {}, run, {})
    check("both captures empty settles PAR-07", verdict == engine.PASS,
          "got %s: %s" % (verdict, detail))

    run["parity"]["after"][0]["animations"] = [{"sel": "div.new"}]
    verdict, detail = engine.CHECKS["parity_motion"]({}, {}, run, {})
    check("a new animation still fails", verdict == engine.FAIL,
          "got %s: %s" % (verdict, detail))


def ledger_cases(engine):
    ext = engine.LEDGER_EXT
    for suffix in (".woff", ".woff2", ".ttf", ".otf", ".eot"):
        check("ledger excludes %s" % suffix, suffix not in ext,
              "a font binary cannot be opened by the harness, so a ledger entry "
              "for one could only be attested rather than earned")
    for suffix in (".jsx", ".css", ".html", ".json", ".png", ".webp", ".svg"):
        check("ledger still covers %s" % suffix, suffix in ext,
              "dropping fonts must not drop anything a reader can be held to")


def modulepreload_cases(engine):
    """JSB-08: the hint that warms the chunk, whichever kind of script it is.

    modulepreload is defined for module scripts. A webpack build that appends a
    plain script per chunk warms the same request with `preload as=script`, and
    asking for modulepreload there would fetch under different credentials and
    parsing rules than the request it stands in for. Accepting only the module
    form makes the gate unpassable for every classic build, and passable only by
    emitting the wrong hint.
    """
    def html_verdict(html):
        project = {"assets": {"a.js": {"kind": "js"}, "b.js": {"kind": "js"}},
                   "entry_html": (Path("build/index.html"), html, True)}
        return engine.check_modulepreload({"id": "JSB-08"}, project, {}, {})

    original = engine.entry_html
    try:
        for name, html, want in (
            ("module build",
             '<head><link rel="modulepreload" href="/a.js"></head>', engine.PASS),
            ("classic build",
             '<head><link rel="preload" as="script" href="/359.chunk.js"></head>', engine.PASS),
            ("classic build, attributes reversed",
             '<head><link as="script" rel="preload" href="/359.chunk.js"></head>', engine.PASS),
            ("no hint at all",
             '<head><link rel="stylesheet" href="/a.css"></head>', engine.FAIL),
            ("a preload that is not a script",
             '<head><link rel="preload" as="font" href="/a.woff2" crossorigin></head>',
             engine.FAIL),
        ):
            engine.entry_html = lambda project, _h=html: (
                Path("build/index.html"), _h, True)
            status, note = html_verdict(html)
            check("JSB-08 on a %s" % name, status == want,
                  "wanted %s, got %s: %s" % (want, status, note))
    finally:
        engine.entry_html = original


def main():
    engine = load_engine()
    if engine is None:
        print("perf-pass: the skill is not installed, so its cases cannot run")
        return 1
    try:
        run_cases(engine)
        unmeasured_cases(engine)
        ledger_cases(engine)
        parity_motion_cases(engine)
        modulepreload_cases(engine)
    except Exception as exc:
        print("perf-pass cases raised: %s" % exc)
        return 1
    if FAILURES:
        print("perf-pass: %d case(s) failed" % len(FAILURES))
        for line in FAILURES[:25]:
            print("  %s" % line)
        return 1
    print("perf-pass: every case holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
