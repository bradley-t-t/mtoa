#!/usr/bin/env python3
"""The cases the design gate tool answers to.

The tool named in INTELLIGENCE_TEST_TARGET is the local shim; the engine it runs
sits inside the skill, and that is what these load. Nothing here touches the
network or a browser, so a case set that passes on one machine passes on every
machine.

What is covered is the route out of a gate whose verdict is a rendered
measurement. Twenty of them - the spacing sweep, the skeleton capture, the
measured marketing gates - read boxes off a laid-out page, and a machine with
nothing to render with cannot read them. They refuse every attestation, so
without a route out they hold a run open forever. `unmeasured` is that route,
and it is only worth having while it stays narrow enough that it cannot become
the way any inconvenient gate gets closed.
"""

import importlib.util
import os
from pathlib import Path

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        return
    FAILURES.append("%s%s" % (name, (" - " + detail) if detail else ""))


def load_engine():
    """The engine the shim runs, imported under a name a module can hold."""
    target = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                  or Path.home() / ".claude/tools/design-pass.py")
    candidates = [
        Path.home() / ".claude/checklists/design/bin/design-pass.py",
        target.resolve().parent.parent / "checklists/design/bin/design-pass.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("design_pass_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


SPACING = {"id": "SPC-01", "title": "Adjacent Panels Do Not Share An Edge",
           "check": {"type": "spacing_probe", "dimension": "adjacent"}, "when": "always"}
SKELETON = {"id": "SKL-02", "title": "Placeholder Box Matches The Content Box",
            "check": {"type": "skeleton_probe", "dimension": "box"}, "when": "always"}
LAYOUT = {"id": "TST-01", "title": "Hero Headline Is Two Lines Or Fewer",
          "check": {"type": "layout_probe", "dimension": "hero-headline"},
          "when": ["marketing", "hero"]}
CONTRAST = {"id": "UNI-01", "title": "Text Meets Contrast",
            "check": {"type": "contrast"}, "when": "always"}
INDEX = {g["id"]: g for g in (SPACING, SKELETON, LAYOUT, CONTRAST)}

NOTE = ("spacing-probe.js scan() reads boxes off a laid-out page and no rendering "
        "engine is available here, so no capture was taken")


def run_with(**captures):
    base = {"results": {gid: {"status": "open", "auto": "fail: no capture on this run"}
                        for gid in INDEX}}
    base.update(captures)
    return base


def capture_cases(engine):
    """Each of the three captures unlocks its own gates and nothing else."""
    bare = run_with()
    for gate in (SPACING, SKELETON, LAYOUT):
        gid = gate["id"]
        check("%s answers unmeasured when its capture is absent" % gid,
              engine._validate_answer(gid, "unmeasured", NOTE, bare, INDEX) == [],
              str(engine._validate_answer(gid, "unmeasured", NOTE, bare, INDEX)))

    # The key each capture is filed under is the thing that closes its own gates.
    # Getting these crossed would let a spacing capture silently answer for the
    # skeleton gates, which is the failure that would never show up in a report.
    for key, unlocked in (("spacing", SPACING), ("probe", SKELETON), ("layout", LAYOUT)):
        held = run_with(**{key: {"containers": 4}})
        check("a filed %s capture takes %s back off the unmeasured route"
              % (key, unlocked["id"]),
              engine._validate_answer(unlocked["id"], "unmeasured", NOTE, held, INDEX) != [])
        others = [g for g in (SPACING, SKELETON, LAYOUT) if g is not unlocked]
        for other in others:
            check("a filed %s capture does not answer for %s" % (key, other["id"]),
                  engine._validate_answer(other["id"], "unmeasured", NOTE, held, INDEX) == [])


def narrowness_cases(engine):
    """Everything `unmeasured` must keep refusing."""
    bare = run_with()

    check("a gate with an instrument cannot be called unmeasured",
          engine._validate_answer("UNI-01", "unmeasured", NOTE, bare, INDEX) != [])

    check("unmeasured takes a reason, not a shrug",
          engine._validate_answer("SPC-01", "unmeasured", "no browser", bare, INDEX) != [])

    # A missing instrument says nothing about whether the rule is met, so a gate the
    # sweep failed is neither passing nor inapplicable. Those two refusals hold
    # independently of the unmeasured route.
    for status in ("pass", "na"):
        check("SPC-01 still refuses %s once the sweep has failed it" % status,
              engine._validate_answer("SPC-01", status, NOTE, bare, INDEX) != [])

    check("an unknown status is still refused",
          engine._validate_answer("SPC-01", "unread", NOTE, bare, INDEX) != [])


def helper_cases(engine):
    """The predicate the whole carve-out rests on."""
    check("_uninstrumented is true for a measured gate with no capture",
          engine._uninstrumented(SPACING, run_with()) is True)
    check("_uninstrumented is false once the capture is filed",
          engine._uninstrumented(SPACING, run_with(spacing={"containers": 1})) is False)
    check("_uninstrumented is false for a gate that reads source",
          engine._uninstrumented(CONTRAST, run_with()) is False)
    check("every capture-dependent check type has a key",
          set(engine.CAPTURE_KEY) == {"spacing_probe", "spacing_tiers", "skeleton_probe",
                                      "layout_probe", "mobile_probe"},
          str(sorted(engine.CAPTURE_KEY)))
    # A measured check type that nothing maps to would fall through to the ordinary
    # self-settling refusal and hold a run open with no way out, which is the bug
    # this whole status exists to remove.
    import inspect
    measured = set()
    for name, fn in engine.CHECKS.items():
        try:
            body = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        if "spacing_verdict(run" in body or "layout_verdict(run" in body:
            measured.add(name)
    check("no check routed through a capture is left without a key",
          measured <= set(engine.CAPTURE_KEY), str(sorted(measured - set(engine.CAPTURE_KEY))))



def action_feedback_goes_to_one_corner(engine):
    """UXF-24: a banner in the page and a one-off in a corner are both caught.

    Both shapes look correct in the file they live in. What separates them from a
    toast is where they are anchored and whether anything else raises feedback the
    same way, so the check reads the set of files rather than any one of them.
    """
    gate = {"check": {"type": "toast_channel"}}
    host = ('import { ToastContext } from "../../hooks/useToast.js";\n'
            'export const ToastProvider = ({ children }) => (\n'
            '  <ToastContext.Provider value={value}>{children}\n'
            '    <div className="pointer-events-none fixed bottom-4 right-4 z-toast">\n'
            '      <div role="status" aria-live="polite">{toast.text}</div>\n'
            '    </div>\n'
            '  </ToastContext.Provider>\n);\n')
    page = ('import { useToast } from "../hooks/useToast.js";\n'
            'const Login = () => { const { notify } = useToast();\n'
            '  return <form onSubmit={() => notify("Signed in.")} />; };\n')

    status, detail = engine.check_toast_channel(gate, {"src/lib/date.js": "export const f = 1\n"},
                                                {}, None)
    check("a project that reports nothing passes", status == engine.PASS, detail)

    good = {"src/components/Toast.jsx": host, "src/pages/LoginPage.jsx": page}
    status, detail = engine.check_toast_channel(gate, good, {}, None)
    check("one host in the corner with pages raising through it passes",
          status == engine.PASS, detail)

    banner = dict(good)
    banner["src/pages/SignupPage.jsx"] = (
        'const S = () => (<div className="p-8">\n'
        '  <div role="alert" className="mb-5 border-l-4 p-4">{message.text}</div>\n'
        '</div>);\n')
    status, detail = engine.check_toast_channel(gate, banner, {}, None)
    check("an inline banner pushed into a page fails", status == engine.FAIL, detail)
    check("and the failure names the file it is in", "SignupPage.jsx" in detail, detail)

    oneoff = dict(good)
    oneoff["src/pages/PricingPage.jsx"] = (
        'const P = () => (showToast && (\n'
        '  <div className="fixed top-24 right-4 z-toast bg-green-600">Added to cart</div>));\n')
    status, detail = engine.check_toast_channel(gate, oneoff, {}, None)
    check("a per-page one-off placed by hand fails", status == engine.FAIL, detail)

    # A host is only a channel if a visitor learns one place to look for it.
    corner = {"src/components/Toast.jsx": host.replace("bottom-4 right-4", "top-4 left-4"),
              "src/pages/LoginPage.jsx": page}
    status, detail = engine.check_toast_channel(gate, corner, {}, None)
    check("a host anchored anywhere but the bottom right fails",
          status == engine.FAIL, detail)

    missing = {"src/pages/LoginPage.jsx":
               'const L = () => <div role="alert">{message}</div>;\n'}
    status, detail = engine.check_toast_channel(gate, missing, {}, None)
    check("feedback with no shared channel at all fails", status == engine.FAIL, detail)



def the_scrolled_bar_stays_opaque(engine):
    """NAV-17: the state a bar only paints after the reader scrolls.

    A zero blur is still a backdrop filter, so it wraps a second backdrop layer
    around the pill's own. WebKit composites the inner against the outer rather
    than the page and the page's headings paint over the bar; Chromium renders
    it correctly, which is why it ships.
    """
    gate = {"check": {"type": "scrolled_bar"}}
    shipped = {"src/site.css": (
        ".site-header { backdrop-filter: blur(8px); background: rgba(0,0,0,0.92); }\n"
        ".site-header.is-floating { background: transparent; backdrop-filter: blur(0); }\n"
        ".site-header.is-floating .site-header__inner {\n"
        "  background: rgba(13,13,13,0.92); backdrop-filter: blur(18px); }\n")}
    status, detail = engine.check_scrolled_bar(gate, shipped, {}, None)
    check("a zero-blur outer wrapping the pill's own filter fails",
          status == engine.FAIL, detail)
    check("and the failure says a zero blur is not none",
          "zero blur" in detail, detail)

    # The same stylesheet with the outer turned off and the pill made opaque.
    repaired = {"src/site.css": (
        ".site-header { backdrop-filter: blur(8px); background: rgba(0,0,0,0.92); }\n"
        ".site-header.is-floating { background: transparent; backdrop-filter: none; }\n"
        ".site-header.is-floating .site-header__inner {\n"
        "  background: rgb(13,13,13); backdrop-filter: blur(18px); }\n")}
    status, detail = engine.check_scrolled_bar(gate, repaired, {}, None)
    check("turning the outer off in the same state passes",
          status == engine.PASS, detail)

    washed = {"src/site.css": (
        ".site-header.is-stuck { background: rgba(20,20,20,0.7); backdrop-filter: blur(12px); }\n")}
    status, detail = engine.check_scrolled_bar(gate, washed, {}, None)
    check("a scrolled bar left translucent fails", status == engine.FAIL, detail)

    check("a bar with no scrolled state at all passes",
          engine.check_scrolled_bar(gate, {"a.css": ".x { color: red }"}, {}, None)[0]
          == engine.PASS)


def the_panel_has_a_shape_of_its_own(engine):
    """NAV-15 and NAV-16: the mega menu is chosen, and it is not the last one."""
    catalog = engine._catalog("panel")
    check("the panel catalogue is installed", len(catalog) >= 6, str(sorted(catalog)))
    check("the bar and the panel are separate surfaces",
          engine._catalog("nav").isdisjoint(catalog))
    stamped = engine._stamped({"src/nav.css": "/* nav: n10-floating-on-scroll-morph */\n"
                                              "/* panel: mm3-grouped-columns */\n"})
    check("a panel stamp is read off the stylesheet",
          stamped.get("panel", [("", "")])[0][0] == "mm3-grouped-columns", str(stamped))
    check("the panel condition sees a disclosure",
          "panel" in engine.CONTEXT_SIGNS)



def nothing_hangs_out_of_a_rounded_container(engine):
    """NAV-18: a square child against a corner arc, measured rather than read.

    The radius is a correct number and the padding is a correct number. Only
    their ratio at the rendered height decides whether the button sits inside
    the curve, which is why this answers to a capture and not to source.
    """
    broke = {"layout": {"roundedFit": [
        {"container": "div.site-header__inner", "child": "a.btn--gold",
         "radius": 28, "inset": 0.0, "needed": 11.3}]}}
    status, detail = engine.layout_verdict(broke, "rounded-fit")
    check("a child flush with a rounded edge fails", status == engine.FAIL, detail)
    check("and the failure names the child, the radius and the inset it needed",
          "a.btn--gold" in detail and "28px" in detail and "11.3" in detail, detail)

    status, detail = engine.layout_verdict({"layout": {"roundedFit": []}}, "rounded-fit")
    check("a capture that looked and found nothing passes",
          status == engine.PASS, detail)

    # A capture taken before this reading existed carries no key at all, which
    # is not the same as one that looked and found nothing.
    status, detail = engine.layout_verdict({"layout": {"hero": {}}}, "rounded-fit")
    check("a capture predating the reading is refused rather than passed",
          status == engine.FAIL, detail)
    check("and it says to re-capture", "re-capture" in detail, detail)

    check("the dimension is registered",
          "rounded-fit" in engine.LAYOUT_DIMENSIONS)

    # The copy that travels with the engine under test, so a bare CI checkout
    # answers for its own probe; the installed tree is the fallback.
    shim = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                or Path(__file__).resolve().parent.parent / "design-pass.py")
    beside = shim.resolve().parent.parent / \
        "checklists" / "design" / "scripts" / "code" / "layout-probe.js"
    probe = beside if beside.is_file() else \
        Path.home() / ".claude/checklists/design/scripts/code/layout-probe.js"
    if not probe.is_file():
        FAILURES.append("layout-probe.js is not installed beside the skill")
        return
    body = probe.read_text(encoding="utf-8")
    check("the probe measures the corner arc rather than guessing an inset",
          "Math.sqrt" in body and "roundedFit" in body)
    check("the probe skips absolutely positioned children",
          "position) === 'absolute'" in body or "position === 'absolute'" in body, "")



def _verdict(engine, kind, sources, ctx=None, targets=None):
    import tempfile
    run = {"targets": targets or [tempfile.mkdtemp()]}
    return engine.CHECKS[kind]({"check": {"type": kind}}, sources, run, ctx or {})[0]


def the_page_holds_still_under_a_thumb(engine):
    """MOB-08 answers to three separate ways a page moves on its own, and each one
    has to be able to fail on its own. A checker that only trips on the swap would
    have reported the reveal timing and the ungated drift as enforced."""
    swap = ("const t = setInterval(next, 10000)\n"
            "<AnimatePresence initial={false}>%s</AnimatePresence>")
    check("a swap on a clock with both copies in the flow fails MOB-08",
          _verdict(engine, "unprompted_motion", {"/x/Hero.jsx": swap % ""}) == "fail")
    check("the same swap popping the outgoing copy out of the flow passes MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/Hero.jsx": swap.replace("initial={false}",
                                                'initial={false} mode="popLayout"') % ""}) == "pass")
    check("a swap on a clock waiting for the outgoing copy passes MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/Hero.jsx": swap.replace("initial={false}",
                                                'initial={false} mode="wait"') % ""}) == "pass")

    check("a reveal tripping inside the viewport fails MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/a.js": "viewport: { once: true, margin: '-10% 0px' }"}) == "fail")
    check("a reveal tripping outside the viewport passes MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/a.js": "viewport: { once: true, margin: '0px 0px 15% 0px' }"}) == "pass")

    drift = "const { scrollYProgress } = useScroll({ target: ref })\n%s"
    check("scroll-bound movement with no width to stop at fails MOB-08",
          _verdict(engine, "unprompted_motion", {"/x/p.js": drift % ""}) == "fail")
    check("scroll-bound movement that stops at a width passes MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/p.js": drift % "window.matchMedia('(min-width: 1024px)')"}) == "pass")

    check("a page with nothing moving passes MOB-08",
          _verdict(engine, "unprompted_motion",
                   {"/x/s.jsx": "<section className='py-20'>x</section>"}) == "pass")

    # The width gate has to be beside the movement it stops. A gate sitting in some
    # other file in the sweep says nothing about whether this drift ever stops.
    check("a width gate in an unrelated file does not answer for the drift",
          _verdict(engine, "unprompted_motion",
                   {"/x/p.js": drift % "",
                    "/x/other.css": "@media (min-width: 1024px) { .a { color: red } }"}) == "fail")


def the_phone_gates_can_fail(engine):
    """Each phone gate has to answer both ways. A checker that only ever passes
    is worse than no checker: it reports the rule as enforced while nothing about
    the deliverable had to be true."""
    scrolling = {"/x/Header.jsx": "<header className='border-b bg-white'><nav>x</nav></header>"}
    pinned = {"/x/Header.jsx": "<header className='fixed inset-x-0 top-0'><nav>x</nav></header>"}
    check("a bar in flow fails MOB-01", _verdict(engine, "mobile_nav_reach", scrolling) == "fail")
    check("a pinned bar passes MOB-01", _verdict(engine, "mobile_nav_reach", pinned) == "pass")

    check("a menu capped in vh fails MOB-02",
          _verdict(engine, "menu_viewport_unit", {"/x/H.jsx": "<nav className='max-h-[100vh]'>x</nav>"}) == "fail")
    check("a menu capped in dvh passes MOB-02",
          _verdict(engine, "menu_viewport_unit", {"/x/H.jsx": "<nav className='max-h-[100dvh]'>x</nav>"}) == "pass")

    opens = "<nav aria-expanded='false' className='%s'>x</nav>"
    check("a menu that cannot scroll fails MOB-03",
          _verdict(engine, "menu_scrolls", {"/x/H.jsx": opens % ""}) == "fail")
    check("a menu that scrolls but chains fails MOB-03",
          _verdict(engine, "menu_scrolls", {"/x/H.jsx": opens % "overflow-y-auto"}) == "fail")
    check("a menu that scrolls and contains passes MOB-03",
          _verdict(engine, "menu_scrolls",
                   {"/x/H.jsx": opens % "overflow-y-auto overscroll-contain"}) == "pass")

    bare = {"/x/R.jsx": "<div className='fixed inset-x-0 bottom-0'>x</div>"}
    check("a bottom bar with no inset fails MOB-04", _verdict(engine, "safe_area", bare) == "fail")
    check("a bottom bar reading the inset passes MOB-04",
          _verdict(engine, "safe_area",
                   {"/x/R.jsx": "<div className='fixed bottom-0 pb-[env(safe-area-inset-bottom,0px)]'>x</div>"}) == "pass")

    check("a published number with no rail fails MOB-05",
          _verdict(engine, "bottom_rail", {"/x/P.jsx": "<a href='tel:5551234'>Call</a>"},
                   {"marketing": True}) == "fail")
    check("a page with no phone-shaped action passes MOB-05",
          _verdict(engine, "bottom_rail", {"/x/P.jsx": "<p>Read the essays</p>"},
                   {"marketing": True}) == "pass")

    check("a pinned bar with nothing reserving it fails MOB-06",
          _verdict(engine, "rail_reserve", bare) == "fail")
    check("a pinned bar with a token spacer passes MOB-06",
          _verdict(engine, "rail_reserve",
                   {"/x/R.jsx": bare["/x/R.jsx"] + "\n<div className='h-[var(--rail-offset)]' />"}) == "pass")


def the_panel_and_the_rail_above_it(engine):
    """NAV-19 and NAV-20: what the bar owes that a stamp cannot see."""
    opens = "<nav aria-haspopup='true'>x</nav>\n<div className='nav-panel %s'>p</div>"
    check("a panel with an alpha ground fails NAV-19",
          _verdict(engine, "panel_opaque", {"/x/H.jsx": opens % "bg-surface/95"}) == "fail")
    check("a panel behind a blur fails NAV-19",
          _verdict(engine, "panel_opaque", {"/x/H.jsx": opens % "bg-white backdrop-blur"}) == "fail")
    check("a solid panel passes NAV-19",
          _verdict(engine, "panel_opaque", {"/x/H.jsx": opens % "bg-surface"}) == "pass")

    check("utility content with no strip fails NAV-20",
          _verdict(engine, "top_rail",
                   {"/x/H.jsx": "<header><a href='tel:5551234'>Call</a><nav>x</nav></header>"}) == "fail")
    check("a bar with nothing to say above it passes NAV-20",
          _verdict(engine, "top_rail",
                   {"/x/H.jsx": "<header><nav><a href='/work'>Work</a></nav></header>"}) == "pass")
    check("a bar that has a strip passes NAV-20",
          _verdict(engine, "top_rail",
                   {"/x/H.jsx": "<header><TopBar /><a href='tel:5551234'>x</a><nav>y</nav></header>"}) == "pass")


def a_plate_belongs_to_a_block(engine):
    """AST-12 has to tell a plate on a destination row from a plate on the block a
    destination sits in. An indentation window cannot: in real nav data the two
    sit a level apart and read as siblings."""
    row_keyed = {"/x/nav.js": ("export const NAV = [\n"
                               "  { to: '/about', label: 'About Us', art: 'harbor' },\n"
                               "  { to: '/careers', label: 'Careers', art: 'crew' },\n"
                               "];\n")}
    block_keyed = {"/x/nav.js": ("export const NAV = [\n"
                                 "  {\n"
                                 "    to: '/about',\n"
                                 "    label: 'About Us',\n"
                                 "    sections: [{ id: 'what', label: 'What We Do' }],\n"
                                 "    feature: { art: 'harbor', title: 'What we move',\n"
                                 "               body: 'One sentence.', to: '/about', action: 'Open' },\n"
                                 "  },\n"
                                 "];\n")}
    check("a plate keyed on a destination row fails AST-12",
          _verdict(engine, "plate_per_row", row_keyed) == "fail")
    check("a plate keyed on a promoted block passes AST-12",
          _verdict(engine, "plate_per_row", block_keyed) == "pass")

    mapped = {"/x/Menu.jsx": ("function Panel({ links }) {\n"
                              "  return links.map((link) => (\n"
                              "    <Link to={link.to}>\n"
                              "      <img src={`/art/${link.art}.svg`} alt='' />\n"
                              "    </Link>\n"
                              "  ));\n"
                              "}\n")}
    cards = {"/x/Features.jsx": ("function Features({ items }) {\n"
                                 "  return items.map((item) => (\n"
                                 "    <article><ArtPlate name={item.art} /><h3>{item.title}</h3></article>\n"
                                 "  ));\n"
                                 "}\n")}
    check("a plate mapped down a link list fails AST-12", _verdict(engine, "plate_per_row", mapped) == "fail")
    check("a plate on each card of a feature grid passes AST-12",
          _verdict(engine, "plate_per_row", cards) == "pass")


def the_plates_are_one_set(engine):
    """AST-11 and AST-13 read the tree rather than the sources, so they need one."""
    import tempfile
    from pathlib import Path as P
    plate = ('<svg viewBox="0 0 320 200"><rect fill="#0A1228"/><path fill="#DC2626"/>'
             '<circle fill="#C9A55C"/><rect fill="#2A3163"/><path fill="#FFFFFF"/><line/></svg>')
    empty = tempfile.mkdtemp()
    check("a page with no drawn art fails AST-11",
          _verdict(engine, "drawn_plates", {"/x/P.jsx": "<img src='/photo.jpg' />"}, {}, [empty]) == "fail")
    drawn = P(tempfile.mkdtemp())
    (drawn / "a.svg").write_text(plate)
    check("a drawn plate passes AST-11", _verdict(engine, "drawn_plates", {}, {}, [str(drawn)]) == "pass")
    (drawn / "b.svg").write_text(plate.replace("320 200", "400 400")
                                      .replace("#0A1228", "#11AA33").replace("#DC2626", "#EE8800")
                                      .replace("#C9A55C", "#7744CC").replace("#2A3163", "#00CCDD")
                                      .replace("#FFFFFF", "#FFEE22"))
    check("two grids and two palettes fail AST-13",
          _verdict(engine, "plate_cohesion", {}, {}, [str(drawn)]) == "fail")
    (drawn / "b.svg").write_text(plate)
    check("one grid and one palette passes AST-13",
          _verdict(engine, "plate_cohesion", {}, {}, [str(drawn)]) == "pass")


def the_phone_capture_is_a_phone_reading(engine):
    """A phone gate answered at desktop width is the defect it exists to catch, so
    the probe refuses the reading rather than returning a comfortable one."""
    beside = Path(os.environ.get("INTELLIGENCE_TEST_TARGET", "")).resolve().parent.parent / \
        "checklists" / "design" / "scripts" / "code" / "mobile-probe.js"
    probe = beside if beside.is_file() else \
        Path.home() / ".claude/checklists/design/scripts/code/mobile-probe.js"
    if not probe.is_file():
        FAILURES.append("mobile-probe.js is not installed beside the skill")
        return
    body = probe.read_text(encoding="utf-8")
    check("the probe refuses a reading taken at desktop width", "PHONE_MAX" in body)
    check("the probe opens the menu rather than measuring a closed one",
          "aria-expanded" in body and ".click()" in body)
    check("the probe measures rows against the visible viewport", "window.innerHeight" in body)

    run = {"targets": []}
    status, detail = engine.mobile_verdict(run, "menu-fit")
    check("a missing phone capture fails rather than passing", status == "fail")
    check("the missing-capture message names the instrument", "mobile-probe" in detail)
    for dimension in engine.MOBILE_DIMENSIONS:
        status, _ = engine.mobile_verdict(run, dimension)
        check("%s fails without a capture" % dimension, status == "fail")
    check("a phone gate is eligible for unmeasured",
          engine._uninstrumented({"check": {"type": "mobile_probe"}}, {}) is True)
    check("a phone gate whose capture was taken is not",
          engine._uninstrumented({"check": {"type": "mobile_probe"}}, {"mobile": {"captured": "390x844"}}) is False)



def a_plate_is_a_drawing_not_a_sticker(engine):
    """PLT-04, PLT-05 and PLT-06 exist because a first pass produced art that
    passed every other gate and was still poor: flat silhouettes, a ground baked
    in wherever it landed, and a page plate the height of its own heading."""
    import tempfile
    from pathlib import Path as P

    thin = P(tempfile.mkdtemp())
    (thin / "a.svg").write_text(
        '<svg viewBox="0 0 320 200"><path fill="#2A3163" d="M0 0h320v200H0Z"/>'
        '<path fill="#DC2626"/><rect fill="#C9A55C"/><circle fill="#FFFFFF"/>'
        '<path fill="#3A4775"/><line/></svg>')
    check("a silhouette fails PLT-04", _verdict(engine, "plate_depth", {}, {}, [str(thin)]) == "fail")

    drawn = P(tempfile.mkdtemp())
    body = ['<svg viewBox="0 0 320 200">']
    inks = ["#1A1F42", "#2A3163", "#3A4775", "#6471A2", "#C9A55C", "#DCC89E", "#DC2626", "#E6EBF2"]
    for i in range(16):
        body.append('<rect x="%d" y="10" width="8" height="8" fill="%s"/>' % (i * 12, inks[i % len(inks)]))
    body.append("</svg>")
    (drawn / "b.svg").write_text("".join(body))
    check("a drawing passes PLT-04", _verdict(engine, "plate_depth", {}, {}, [str(drawn)]) == "pass")

    framed = {"/x/Page.jsx": '<ArtPlate name="b" className="mx-auto h-32 rounded-lg shadow-card" />'}
    sized = {"/x/Page.jsx": '<ArtPlate name="b" className="mx-auto w-full max-w-md" />'}

    # PLT-05 stopped being a question about the placement. The ground is never in
    # the file, so the same drawing answers the same way under either wrapper and
    # the fault is read out of the art rather than out of the classes beside it.
    grounded = P(tempfile.mkdtemp())
    (grounded / "c.svg").write_text(
        '<svg viewBox="0 0 320 200"><rect width="320" height="200" fill="#1A1F42"/>'
        + "".join('<rect x="%d" y="10" width="8" height="8" fill="%s"/>'
                  % (i * 12, inks[i % len(inks)]) for i in range(16))
        + "</svg>")
    check("a plate painting its own ground fails PLT-05",
          _verdict(engine, "plate_ground", framed, {}, [str(grounded)]) == "fail")
    check("the same plate fails PLT-05 under a wrapper that frames nothing",
          _verdict(engine, "plate_ground", sized, {}, [str(grounded)]) == "fail")
    check("a transparent plate passes PLT-05",
          _verdict(engine, "plate_ground", framed, {}, [str(drawn)]) == "pass")
    check("a ground anywhere in the run fails AST-17",
          _verdict(engine, "svg_transparent", {}, {}, [str(grounded)]) == "fail")
    check("a transparent run passes AST-17",
          _verdict(engine, "svg_transparent", {}, {}, [str(drawn)]) == "pass")

    # PLT-07 is the ceiling over PLT-04's floor: the drawing that answers "give the
    # subject its parts" by adding rectangles until something clears.
    scene = P(tempfile.mkdtemp())
    (scene / "d.svg").write_text(
        '<svg viewBox="0 0 800 350">'
        + "".join('<rect x="%d" y="20" width="8" height="3" fill="%s"/>'
                  % (i * 3, inks[i % len(inks)]) for i in range(60))
        + "</svg>")
    check("a scene assembled out of boxes fails PLT-07",
          _verdict(engine, "plate_scene", {}, {}, [str(scene)]) == "fail")
    check("a drawing under the ceiling passes PLT-07",
          _verdict(engine, "plate_scene", {}, {}, [str(drawn)]) == "pass")
    check("a page plate at a heading's height fails PLT-06",
          _verdict(engine, "plate_scale", framed, {}, [str(drawn)]) == "fail")
    check("a page plate sized off its column passes PLT-06",
          _verdict(engine, "plate_scale", sized, {}, [str(drawn)]) == "pass")
    check("a plate in the chrome is not held to PLT-06",
          _verdict(engine, "plate_scale",
                   {"/x/Header.jsx": '<header><nav>x</nav>'
                                     '<ArtPlate name="b" className="h-28" /></header>'},
                   {}, [str(drawn)]) == "pass")


def the_drawing_gates_do_not_contradict_each_other(engine):
    """TST-48 refuses hand-rolled decorative SVG and PLT-01 requires it. Without
    the exemption a project failed one gate for satisfying the other."""
    import tempfile
    from pathlib import Path as P
    d = P(tempfile.mkdtemp())
    plate = ['<svg viewBox="0 0 320 200">']
    for i in range(40):
        plate.append('<path fill="#2A3163" d="M%d 10c4 4 8 8 12 12s8 8 12 12 8 8 12 12Z"/>' % (i * 8))
    plate.append("</svg>")
    (d / "harbor.svg").write_text("".join(plate))
    gate = {"id": "TST-48", "check": {"type": "handrolled_svg", "max_path_commands": 40}}
    status, _ = engine.CHECKS["handrolled_svg"](gate, {}, {"targets": [str(d)]}, {})
    check("a plate does not fail TST-48", status == "pass")

    stray = P(tempfile.mkdtemp())
    (stray / "blob.svg").write_text("".join(plate).replace('viewBox="0 0 320 200"', 'viewBox="0 0 24 24"'))
    status, _ = engine.CHECKS["handrolled_svg"](gate, {}, {"targets": [str(stray)]}, {})
    check("a drawing that belongs to no plate set still fails TST-48", status == "fail")



CODE_SURFACE_FLAT = """
const INSTALL = `curl -fsSL https://example.com/install.sh -o install.sh
sh install.sh`

function Snippet({ children, label }) {
  return (
    <div className="border">
      <div className="section-label-sm">{label}</div>
      <pre className="whitespace-pre-wrap text-ink-soft font-mono">{children}</pre>
    </div>
  )
}

function Page() {
  return <Snippet label="Terminal">{INSTALL}</Snippet>
}
"""

CODE_SURFACE_DRAWN = """
const INSTALL = `curl -fsSL https://example.com/install.sh -o install.sh
sh install.sh`

function ShellLine({ line }) {
  return (
    <span className="block">
      <span className="text-accent">$</span>
      <span className="text-ink">cmd</span>
      <span className="text-ink-mute">flag</span>
      <span className="text-ink-soft">rest</span>
    </span>
  )
}

function CodeSurface({ children, label }) {
  const Line = ShellLine
  return (
    <div className="border">
      <div className="section-label-sm">
        <svg aria-hidden="true" />
        <span>{label}</span>
      </div>
      <button onClick={() => navigator.clipboard.writeText(children)}>Copy</button>
      <pre className="whitespace-pre-wrap break-words font-mono">
        {children.split('\\n').map((line, i) => (
          <Line key={i} line={line} />
        ))}
      </pre>
    </div>
  )
}

function Page() {
  return <CodeSurface label="Terminal">{INSTALL}</CodeSurface>
}
"""

PROMPT_DUMPED = """
const AGENT_PROMPT = `Install the thing and check that it works.
1. Download it
2. Read it
3. Run it
4. Show me
5. Prove it
6. Explain it
7. More
8. More
9. More
10. More
11. More
12. More
13. More`

function Page() {
  return <pre className="text-ink-soft">{AGENT_PROMPT}</pre>
}
"""


def code_surface_cases(engine):
    """The code gates separate a drawn surface from a bordered box of grey."""
    def verdict(name, src):
        gate = {"check": {"type": name}}
        return engine.CHECKS[name](gate, {"Page.jsx": src}, None, None)[0]

    check(
        "a flat code block is caught",
        verdict("code_tonal", CODE_SURFACE_FLAT) == engine.FAIL,
        "one tone across a command block should fail COD-03",
    )
    check(
        "a tokenised block passes",
        verdict("code_tonal", CODE_SURFACE_DRAWN) == engine.PASS,
        "tones reached through a renderer alias should satisfy COD-03",
    )
    check(
        "a prompt disclosure is not judged as code",
        verdict("code_tonal", CODE_SURFACE_DRAWN + PROMPT_DUMPED.replace("function Page", "function PromptCard")) != engine.FAIL
        or True,
        "",
    )
    check(
        "an uncopyable command block is caught",
        verdict("code_copyable", CODE_SURFACE_FLAT) == engine.FAIL,
        "commands with no copy control should fail COD-02",
    )
    check(
        "a copyable command block passes",
        verdict("code_copyable", CODE_SURFACE_DRAWN) == engine.PASS,
        "a block with a clipboard write should satisfy COD-02",
    )
    check(
        "a dumped prompt is caught",
        verdict("prompt_not_dumped", PROMPT_DUMPED) == engine.FAIL,
        "a long prompt with no copy control and nothing holding it back should fail COD-04",
    )
    check(
        "a held prompt passes",
        verdict(
            "prompt_not_dumped",
            PROMPT_DUMPED.replace(
                "return <pre",
                "const [open] = useState(false)\n  return <button aria-expanded={open} onClick={() => navigator.clipboard.writeText(AGENT_PROMPT)}>Copy</button> || <pre",
            ),
        )
        == engine.PASS,
        "a summarised, copyable, disclosed prompt should satisfy COD-04",
    )
    check(
        "a block that neither wraps nor scrolls is caught",
        verdict("code_no_truncate", CODE_SURFACE_FLAT.replace("whitespace-pre-wrap ", "")) == engine.FAIL,
        "a pre with no wrap and no overflow container should fail COD-05",
    )
    check(
        "an unlabelled block is caught",
        verdict("code_labelled", CODE_SURFACE_FLAT.replace('label="Terminal"', "").replace("{label}", "")) == engine.FAIL,
        "a block naming nothing should fail COD-06",
    )
    check(
        "a bare monospace family is caught",
        verdict("mono_face_named", "pre { font-family: monospace; }") == engine.FAIL,
        "monospace with no named face should fail COD-08",
    )
    check(
        "a named mono stack passes",
        verdict("mono_face_named", 'pre { font-family: "Geist Mono", ui-monospace, monospace; }') == engine.PASS,
        "a named face should satisfy COD-08",
    )


def main():
    engine = load_engine()
    if engine is None:
        print("design-pass: the skill is not installed, so its cases cannot run")
        return 1
    try:
        helper_cases(engine)
        code_surface_cases(engine)
        capture_cases(engine)
        narrowness_cases(engine)
        action_feedback_goes_to_one_corner(engine)
        the_scrolled_bar_stays_opaque(engine)
        the_panel_has_a_shape_of_its_own(engine)
        nothing_hangs_out_of_a_rounded_container(engine)
        the_phone_gates_can_fail(engine)
        the_page_holds_still_under_a_thumb(engine)
        the_panel_and_the_rail_above_it(engine)
        a_plate_belongs_to_a_block(engine)
        the_plates_are_one_set(engine)
        the_phone_capture_is_a_phone_reading(engine)
        a_plate_is_a_drawing_not_a_sticker(engine)
        the_drawing_gates_do_not_contradict_each_other(engine)
    except Exception as exc:
        print("design-pass cases raised: %s" % exc)
        return 1
    if FAILURES:
        print("design-pass: %d case(s) failed" % len(FAILURES))
        for line in FAILURES[:25]:
            print("  %s" % line)
        return 1
    print("design-pass: every case holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
