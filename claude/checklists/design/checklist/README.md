# The gate registry

Every rule the design skill states lives here as data. `~/.claude/tools/design-pass.py` reads
these files, works out which gates a run has to answer, settles the ones a regex can settle,
and refuses to close while anything is left silent.

The reason it is data rather than prose: prose gets read the way prose gets read. A model
opens `polish.md`, takes what looks relevant, and moves on — and afterwards an unchecked rule
and a passing one are indistinguishable in the output. A gate that is never answered shows up
as an open gate.

## Files

| File | Group | What it holds |
| --- | --- | --- |
| `gates/00-process.json` | Process | The run itself: opened, scoped, verified, reported |
| `gates/10-universal.json` | Universal | The five principles and the standing rules above them |
| `gates/12-reference.json` | Reference Sites | What linear.app, stripe.com, and apple.com agree on, held as rules |
| `gates/13-navigation.json` | Navigation Bars | What stripe, apple, anduril, ramp, mercury, saronic, and shield.ai agree on for the top bar |
| `gates/14-mobile.json` | The Phone | What a phone decides and a desktop review cannot see: a bar reachable from anywhere, a menu measured against the visible viewport, chrome on an edge that clears the device inset and reserves its own height, and a thumb-sized target |
| `gates/15-production.json` | Production Site Hygiene | What the 2026-08 audit of 13 owned sites found the audited sites do that Stripe, Apple, Linear, Vercel, and Anthropic do not — SPA catch-all 200s, canonical pointing at the deployment host, robots.txt served as HTML, empty entity in the footer, dev routes in the public bundle |
| `gates/16-plates.json` | Drawn Plates | Art a project drew for its own subject: what it draws, how much of the subject it must carry and how much it must not, that its ground is never in the file, how big it is allowed to be, and that it is never a mark per menu row |
| `gates/18-email.json` | Email | What an HTML email owes a client that strips stylesheets, scripts, SVG, and fonts |
| `gates/20-pointer.json` | Pointer & Press | What the interface does under a cursor and a finger |
| `gates/22-fields.json` | Fields & Menus | Inputs, selects, and the whole custom-listbox contract |
| `gates/24-tables.json` | Tables | Dense rows, a header that survives the scroll, a small-screen answer |
| `gates/26-headers.json` | Headers | Page headers, section heads, and table heads |
| `gates/27-spacing.json` | Spacing | Whether the space between two rendered things is right |
| `gates/28-cohesion.json` | Cohesion | The gates that judge files against each other |
| `gates/30-polish.json` | Motion & Polish | The animation framework and loading-state rules |
| `gates/35-skeleton.json` | Loading Placeholders | A placeholder held to the box that replaces it |
| `gates/40-slop.json` | Slop Test | The 59 structural gates and the six-axis pre-emit critique |
| `gates/45-taste.json` | Marketing Surfaces | What a landing page owes that an app screen does not |
| `gates/50-ux.json` | UX Guidelines | The intelligence library, scoped by what the target contains |
| `gates/60-system.json` | Tokens & System | Token architecture and component specification |
| `gates/70-brand.json` | Brand | Consistency and asset approval |
| `gates/80-assets.json` | Exported Assets | Logos, icons, banners, social images, CIP output |
| `gates/90-slides.json` | Slides | Presentation-specific rules |

## A gate

```json
{
  "id": "PTR-02",
  "title": "Press Feedback On Every Pressable",
  "rule": "Every pressable element scales down on :active ...",
  "source": "references/polish.md#buttons-must-feel-responsive",
  "kinds": ["web-ui", "component"],
  "when": "interactive",
  "severity": "blocker",
  "check": {"type": "require_regex", "patterns": [":active"], "message": "..."},
  "fix": "Add :active { transform: scale(0.97) } ..."
}
```

`kinds` is what the run is producing; `*` means every kind. `when` is a condition the tool
derives from the target's own contents — `form`, `chart`, `sticky`, `png_export`, and the rest
are set by scanning the files, so a page with no chart never sees a chart gate and a run with
no PNG never sees the transparency gate. Three conditions cannot be read out of source and are
declared instead: `--flag redesign`, `--flag ad`, `--flag print`.

`when` may be a list, and then every condition in it has to hold. `["marketing", "hero"]` is a
hero rule on a landing page, which an app screen with an `<h1>` does not answer for.

`marketing` is the one derived condition that weighs signals rather than matching a pattern. A
landing page and a dashboard are both `web-ui`, and most of the Marketing Surfaces group is
wrong on the second: hero fold, eyebrow density, one accent for the whole page, no scroll cue.
The tool counts landing-page signals (hero, pricing, testimonials, a trust strip, FAQ, waitlist,
selected work) against app signals (a sidebar shell, an auth guard, a session hook, a data grid,
an admin route) and takes the higher. `--flag marketing` forces it on and `--flag app` forces it
off, because the reading is a guess and a wrong guess either fires fifty inapplicable gates at a
dashboard or lets a landing page past every rule written for one.

`severity` is `blocker`, `required`, or `advisory`. Blockers also stop `git commit` and
`gh pr create` while they are open.

A file may carry a `defaults` object; each gate in it inherits and overrides.

## Checks

`check.type` names a function in `design-pass.py`. Three verdicts come back:

- **pass** — the gate is answered and closes on its own.
- **fail** — a defect, with file and line. Fix it; do not record it.
- **inconclusive** — the check found something it cannot judge (labels that may need Title
  Case, a metric that may or may not have come from the user, a handler on a `div`). The gate
  stays open and needs an answer with evidence.

An unimplemented or broken checker returns inconclusive, never pass. That asymmetry is the
whole point: a checker that silently passed would be worse than no checker, because it would
look like coverage.

## The file ledger

Gates answer for the deliverable; the ledger answers for the files. Every file under
the run's targets is listed, and each one leaves the run in one of two states:
changed, or read and ruled as needing no design change with the reason recorded.
Files whose extension cannot carry design are counted as excluded by type and named
in the report, so nothing drops out of the accounting quietly.

```bash
~/.claude/tools/design-pass.py files                 # what is still to be ruled on
~/.claude/tools/design-pass.py files --all           # the whole ledger with verdicts
~/.claude/tools/design-pass.py file-clear <path>... --note "..."
~/.claude/tools/design-pass.py file-clear --batch -   # a note per file, one call
```

Clearing a file requires that the harness saw it read — the Read tool, or `cat`,
`sed`, `head`, and the rest, credited by a `PostToolUse` hook. That requirement is
the whole point: without it, "read and needs nothing" and "never opened" produce the
same entry. `design-pass.py` carries a `file_coverage` check for this, but no gate
file declares it, so nothing in the registry holds a run open over the ledger today.
Until one does, `design-pass.py files` before `finish` is what catches an unread file.

## Answering in batches

A run's wall-clock cost is round trips, not checks. The sweep settles a few hundred
gates in seconds, and answering each of the rest on its own call would cost more
than the sweep did, with a ledger of two hundred files one call each on top.

```bash
~/.claude/tools/design-pass.py status --full          # every open gate with rule, fix, evidence
~/.claude/tools/design-pass.py resolve --batch -      # a status and a note per gate, one call
```

Nothing is relaxed to allow it. Each row is validated on its own against the same
conditions a single answer meets: the note minimum, the refusal to pass a gate the
sweep failed, the refusal to attest to a bookkeeping gate, and membership of this
run. A batch carrying one bad row is refused whole and reports every fault, so a
good answer never lands quietly beside a bad one.

### What a gate cannot be answered with

Four routes out of a gate are closed, in both this tool and its sibling, because
each of them turned a checklist into a survey:

- **`pass` on a gate the sweep failed.** Already refused before this.
- **`na` on a gate the sweep failed.** This was the open door: a gate would fail,
  `pass` would be refused, and `na` closed it with a one-line note. The sweep
  failing is proof the condition is present and unmet, which is the opposite of
  inapplicable.
- **`na` while the tool can still see the condition.** A gate enters a run because
  its condition was found in the target. If it is still there, not-applicable
  contradicts the tool rather than the gate.
- **A note with no evidence.** An assertion is not a note. It has to name a file, a
  line, a measurement, or quote what was read.

A fifth status exists for the case those four would otherwise trap:

```bash
resolve <ID> --status disputed --note "<what the check reported, what is actually
there, and how that was established>"
```

`disputed` says the rule applies and the automated check measured it wrongly. It
closes the gate, so one bad check cannot jam a build, and it leads the report under
its own heading, because every dispute is either a defect in the check or a rule
that went unenforced. It takes a longer note than the other verdicts and it is
refused on a gate the sweep did not fail.

### The gate for generic

Every other rule in the set measures whether a surface is free of defects. A bar
can pass all of them and still be the shape a generated page reaches for first,
with the project's tokens painted on: `SLP-42` reads surface polish and structural
richness, and a wordmark-left, seven-link, one-CTA bar with a blur satisfies both.

So the shape itself is a gate. The catalog of 14 navigation archetypes and 8 footer
archetypes lives in `references/hallmark/references/components`, and chrome
declares which one it is:

```css
/* nav: n5-floating-pill */
/* footer: ft5-statement */
```

Four things are then checkable, and all four are:

- **NAV-11 / FTR-01** - the stamp exists. No stamp means the shape was arrived at
  rather than chosen, and no other gate can see the difference.
- The stamp names a real archetype. An invented slug is refused against the
  catalog, so the stamp cannot be a sticker.
- **NAV-12 / FTR-02** - the named shape is not the default. `n1b-saas-three-section`,
  `n1-wordmark-2-links`, and `ft3-index-style-category-list` are where a page lands
  when nobody decided.
- **NAV-13** - the shape differs from the last run of the same page kind, recorded
  when a run closes. Two landing pages sharing a nav archetype are one template
  wearing two palettes.

The escape is `--status disputed` with the reason the default shape is right for
this brief, which puts the claim at the top of the report instead of closing the
gate quietly.

## Measured gates

Most checks read source. A few read a measurement, because the thing they judge is
not visible in source at all: whether a placeholder actually lands on the box that
replaces it. Those gates (SKL-02 through SKL-16) read a capture taken in the
browser — both states measured at one viewport, diffed by the tool — and they fail
until one exists:

```bash
# In the page, with the loading state on screen, then again once it has landed:
#   skeletonProbe.capture('loading', {...}); skeletonProbe.capture('loaded', {...})
#   skeletonProbe.result()
~/.claude/tools/design-pass.py skeleton-probe --file capture.json
```

The helper is `scripts/code/skeleton-probe.js`. The point of routing these through
data rather than an attestation is that "the skeleton matches" is the single
easiest thing in a design review to believe without checking.

The marketing gates measure a second set of things source cannot hold: how many
lines a headline wrapped to at the font scale that shipped, whether the primary
CTA survived above the fold, whether the navigation stayed on one row at 1024px,
and how tall the bar came out. TST-01, TST-02, TST-60, and TST-61 read that
capture and fail until one exists:

```bash
# In the page at desktop width:
#   layoutProbe.scan()
~/.claude/tools/design-pass.py layout-probe --file capture.json
```

The helper is `scripts/code/layout-probe.js`.

The phone gates measure a third set, and the reason they cannot be read from source is
that what breaks them is not in the page: the browser's own retracting toolbar, the home
indicator under it, and the width of a thumb. MOB-10 through MOB-15 read a capture taken
at a phone width with the menu actually opened, and fail until one exists:

```bash
# In the page at 390x844. It opens the menu itself, so it returns a promise:
#   await mobileProbe.scan()
~/.claude/tools/design-pass.py mobile-probe --file capture.json
```

The helper is `scripts/code/mobile-probe.js`. It refuses a reading taken wider than 500px,
because a phone gate answered at desktop width is the defect it exists to catch.

## The design read

TST-62 holds a marketing run open until the brief has been read and the reading
recorded, because a model that skips that step reaches for a default aesthetic
instead of the one the audience calls for:

```bash
~/.claude/tools/design-pass.py read --page-kind landing \
  --audience "fleet operations managers" --vibe "plain, industrial, no gloss" \
  --palette "cold luxury" --face "GT Walsheim"
```

`--palette` and `--face` feed TST-63, which compares the run against the last one
of the same page kind and refuses a repeat. That history is written when a run
closes, not when the read is recorded, so an abandoned run never becomes the
direction the next one has to differ from.

## The gate cache

Verify keeps a cache of automated passes so a repeat run only re-checks what could have flipped. It is held by `tools/pass_cache.py`, shared with the other checklists, at `~/.claude/state/pass-cache/design/design-<hash of the run's targets>.json`. It is per-machine, so a cache answered on one machine shortens a run only on that machine.

A cache hit needs three things to match: the gate JSON hash (any edit to `check`, `rule`, `severity`, `when`, or `kinds` invalidates it), the sources bundle hash (every source file in scope, path plus content), and the question the run is asking — its kind, its declared flags, its URL and its scope, hashed together, so a narrowed run never answers a whole one. Cross-file gates (`cohesion_*`, `spacing_*`) and runtime-measured gates (`skeleton_*`, and any of the coverage rollups) are marked uncacheable in `UNCACHEABLE_CHECKS` and always re-run, because their result under one source snapshot is not evidence for a later one. Manual gates never cache — they were answered by a person and cannot be re-derived from a hash.

Force a fresh run with `design-pass.py verify --no-cache`. Each cache entry records the run id and timestamp of the pass it was captured from, and whether a sweep or a person answered it, and `verify` prints what was carried at the end of the sweep.

## Adding a gate

Add it to the group file it belongs to, give it a `source` that points at the reference it
came from, and pick the narrowest `when` that is still true. Prefer a real check over
`manual` — a regex that catches four cases out of five is worth more than a line of prose
asking the model to remember. Then confirm it against something that should fail it:

```bash
~/.claude/tools/design-pass.py start --kind web-ui --target <a directory with the defect>
~/.claude/tools/design-pass.py verify
```
