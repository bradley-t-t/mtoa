---
name: inspire
description: Rebuild one page's elements to match the sites that do them best - one element at a time, with me, matching form, motion and behavior rather than the screenshot. References are captured live in Chrome, shown as stills and recordings before anything is built, then rebuilt in this project's own tokens and components. Use on /inspire, or "make this page look like the good sites", "match how linear does this", "element by element on this page", "steal the technique from stripe's checkout".
argument-hint: "<route or file the page lives at> [one element, to do just that one]"
---

# inspire

Take a page this project already has, put it next to the sites that do its parts
best, and rebuild the parts - not by pasting theirs in, but by measuring what
they actually did and building that here, in this project's tokens, components
and voice.

It goes one element at a time and it does not move on without a yes. That is
the whole method. A pass that redesigns a page in one sweep produces something
nobody chose; a pass that stops at every element produces a page somebody
decided on, and the decisions are the part that lasts.

## Match three things, not one

An element people call well-made is three separate achievements:

| Lane | The question |
| :--- | :--- |
| **Form** | What it looks like standing still |
| **Motion** | What it does over time, and what starts it |
| **Behavior** | What it is to a keyboard, a screen reader, a pointer, and to bad data |

Form is the only one that photographs, which is why it is the only one that
usually gets matched - and why rebuilds come out looking right and feeling
wrong. The feel is lanes two and three. **An element is not done until all
three are matched or the open lane is named out loud.** The ledger records
them separately for exactly this reason.

## Refuse without a page

This works on one page. Not a site, not a component library in the abstract.
Without a route or a file, ask which page and stop - do not pick one.

If the page named does not exist in this project, say so and stop. A page that
has to be invented before it can be improved is a different job.

## What it will not do

Edits land in a worktree branch and go no further. It never pushes, never opens
or merges a pull request, never deploys, never touches the database, Stripe or
anything that sends mail. Landing is `/commit`. Releasing is `/merge`. Those
stay yours.

It does not carry anyone's identity across. Technique transfers, identity does
not - the full line is in `references/capture.md` and it is not optional. Logos,
illustration, photography, icon sets, licensed fonts and copy stay where they
are. The test is whether a person could put the two side by side and name the
source; if they could, it went too far and goes back.

## 0. The pieces

```bash
INSP="$HOME/.claude/skills/inspire"
EXTRACT="$INSP/bin/extract.js"      # the three-lane capture, run in the page
LEDGER="$INSP/bin/ledger.py"        # what has been covered, per project
SITES="$INSP/references/sites.md"   # where to look, by element
CAPTURE="$INSP/references/capture.md" # how to capture each lane, and the line
```

Read `capture.md` before the first capture. It is the method; this file is the
loop around it.

## 1. Resolve the page and the working tree

```bash
ROOT=$(git rev-parse --show-toplevel)
PROJECT=$(basename "$ROOT")
BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
```

On `develop` or `main`, cut a worktree off `develop` first, the way `/commit`
expects to find it. Already on a branch in a worktree, work there.

Map the route to the files that render it - the router, the view, the components
it pulls. Read them before looking at anything external. **What we have now is
the baseline, and a comparison against a baseline nobody read is a guess.**

## 2. Learn this project's design before looking outward

Everything captured later gets rebuilt in this vocabulary, so it has to be in
hand first. Read, in this order:

- The token layer - the stylesheet's custom properties and the Tailwind config
  that exposes them. Names, values, and which of them are semantic rather than
  raw.
- Two or three components near the target that are already good. They carry the
  house idiom: how a variant is expressed, where motion lives, how state is
  spelled.
- `~/.claude/checklists/design/references/system/` for the token architecture,
  and `references/code/` for the component patterns already written down.

Write down the register in a sentence before continuing - what this project is,
tonally. It is the filter on every reference chosen later, and an element
matched perfectly in the wrong register is a worse outcome than what it replaced.

## 3. Inventory the page

List the elements on the page in the order a reader meets them. Nav, hero, the
sections in sequence, the controls, the footer. Name them the way
`references/sites.md` names them, so the reference lookup is direct.

Then ask what has already been done, so a resumed run does not re-tread:

```bash
"$LEDGER" remaining --project "$PROJECT" --route "<route>" nav hero features pricing footer
"$LEDGER" status --project "$PROJECT"
```

Show me the list and the order before starting. An argument naming one element
skips straight to that element's round.

## 4. The round

One element. Do not start the next one until this one is recorded.

### a. Read what we have

Screenshot our element as it stands, at desktop and mobile, and read the code
that makes it. Name what is actually wrong with it. **"Generic" is not a
finding** - the finding is that the display type sits at the browser's default
tracking, or that the disclosure has no keyboard path, or that the hover is a
0ms colour swap. A round that cannot name what is wrong should record the
element as fine and move on.

**Save the before to a file, and do it now.** Every breakpoint and theme the
round will be proved at, written to disk, before a single character is edited.
This is the one artifact in the round that cannot be recovered later: once (e)
lands, taking the before again means reverting the work to photograph it. A
round that reaches (f) with nothing to put in the first panel has lost the only
evidence that anything changed, and the floor test cannot be run without it.

### b. Pick three references

From `references/sites.md`, filtered by register. Three, never one: one produces
a copy, three produce a decision, because the places they disagree are where the
choice actually is.

### c. Capture, all three lanes

Follow `references/capture.md` per site. Serial - there is one Chrome. For each
reference the capture is:

- The extractor's output at desktop, at mobile, and in the other theme
- A still of the element
- A recording of whatever it does - hover, open, reveal, scroll - via
  `gif_creator`. **A curve described in words is an opinion; the recording is
  the thing**
- The keyboard walk and the state attributes, driven rather than read
- The reduced-motion answer

Where `sheets.blocked` comes back non-empty the hover rules were not readable,
so drive the states by hand instead. Say which sites that happened on; a capture
with a hole in it that gets written up as complete is the one way this produces
confident nonsense.

### d. Show me, then wait

Send the stills and the recordings - ours first, then the three - with
`SendUserFile`, so they arrive whatever device I am on. Then, in a few lines
per reference, what it actually does differently, split by lane. Numbers, not
adjectives: the tracking, the duration, the curve, the stagger, the focus
behavior.

Then ask which direction, with `AskUserQuestion`. Offer the three, and offer
none of them - "leave this element alone" is a real answer and the round records
it as declined.

**Nothing gets built before that answer.** This is the part that makes it a
collaboration rather than a redesign done at me.

### e. Build it here

**The asking is over.** One question reaches me per element and it was (d):
which direction. Everything downstream of that answer is the builder's -
including, especially, the problems the build turns up. A reference that does
not translate cleanly, a measurement that fights the data, two ways to close a
gap: those are the work, not a second decision to hand back. Deciding them is
what "build it" means.

A menu of options presented at me mid-build is an unfinished round wearing the
costume of collaboration, and it is worse than a wrong choice, because a wrong
choice is visible in the after screenshot and can be reversed in a sentence.
Pick the one you would defend, build it, and say in one line what you picked
over and why. If the choice genuinely turns on something only I know - a
business fact, a plan you cannot see - that is the exception, and it is rare;
almost every mid-build fork is decidable by going and measuring something.

In this project's tokens, components and idiom. Concretely:

- **No raw value that we have a token for.** Not a hex, not a radius, not a
  duration, not a curve. The captured number picks *which* token, or shows that
  the token's value is wrong and should move.
- **A gap gets filled properly, once.** The reference will use a primitive we do
  not have - a spring easing, a stagger utility, a focus trap, a scroll-lock.
  Add it to the system where the system keeps that kind of thing, named our way,
  and use it. Inlining it into this one component is how a codebase ends up with
  four focus traps.
- **Structure first, measurements last.** The order is: what is on which line,
  what sits beside what, what carries the hierarchy, and what the reference
  does *not* have. Only then the numbers. Done the other way round the numbers
  land inside our old layout, which is the single most common way this fails -
  the padding and the radius all match and the element is still the element it
  was. If the reference drew a list where we drew a table, the answer is a
  list.
- **Name the deletions before writing anything.** A reference is as much what
  it left out as what it put in: no column heads, no chip, no second weight, no
  mono. Those are decisions, they are the cheapest part to carry across, and
  they are the part a rebuild silently keeps. Write the list down; each item
  either goes or gets a reason.
- **Read the reference in pixels, not with your eyes.** Sample the capture: the
  ground colour and the fill of the state sitting on it, the mark's width and
  the air beside it, its height, its radius. Write the numbers down before
  writing any code. Every failure this skill has had came from trusting an
  impression where a sample would have settled it in a minute - "their active
  row stands out from the rail" is equally true of a row a shade darker than
  the rail and of a white card floating above it, and those are opposite
  designs.
- **Get the direction of a relationship, not just its presence.** Darker or
  lighter, set in or standing off, tighter or looser. An impression records
  that there is a difference; only a number records which way it goes. A state
  built the wrong way round does not look like a bug - it looks deliberate, and
  reads as a different product.
- **Find which measure is the invariant.** A reference holds an absolute size,
  or a proportion, or a share of its container, and taking the wrong one gives
  something that satisfies the arithmetic and resembles nothing. Matching a
  mark-to-gap ratio and letting both grow to fill the row turned a barcode into
  a row of tiles: the ratio was right to two decimals and the element was gone.
  When it is unclear, the invariant is the absolute one. Their container's
  width is a fact about their page, not a target to be met - if their measure
  leaves ours short, ours is short.
- **A copied measure arrives without the constraint that held it.** Swapping a
  mark that shrank to fit for one at a fixed width imports their sizing and
  drops the fitting, and the element runs out through the side of the card the
  first time the data is longer or the screen narrower than the case it was
  built against. Every fixed measure taken from a reference needs its own
  answer to what happens when there is more of it than fits - clip, drop the
  oldest, wrap or scroll - and that answer gets tested against the largest real
  case, not the typical one.
- **A reference value that fails a floor is declined with its number.** Their
  colour may be under the contrast bar, their target under the touch minimum,
  their motion without a reduced-motion path. Compute the figure, say it, keep
  ours, record the lane as declined rather than matched - and work out the
  closest value to theirs that clears the floor, because that is usually the
  one worth taking.
- **Match the composition, not the markup.** Their DOM is shaped by their
  framework. What carries across is the structure that makes it work - the
  landmark, the labelled control, the state attribute the CSS hangs off.
- **Motion is not decoration and gets the same rigour as the layout.** Duration,
  curve, what triggers it, what it does on close, and the reduced-motion path.
  The close animation left at 0ms is the single most common way a matched
  element still feels broken.
- House rules hold: no emoji anywhere, UI labels Title Case and prose sentence
  case, every SVG transparent behind its drawing, no copy that exists because of
  this conversation, no backup copies of files.

### f. Prove it, in the same three lanes

Run the dev server through `preview_start` using `.claude/launch.json`, then
view it in Chrome - the same browser the references were captured in, so the
screenshots are comparable rather than merely similar.

**Against the real data.** Where the element draws from a feed, an API or a
table, the proof render uses what that source actually holds. A dev server that
cannot reach it is not permission to invent a payload: go and read the live one
- its shape, its volume, how much of it is missing - and use that as the
fixture, saying in the caption that the figures are real but the server is
stubbed.

Invented data is always tidier than the real thing, and tidier in exactly the
way that hides the fault. A made-up month is a full month; the real one turns
out to be a fortnight of readings and a fortnight of nothing, and an element
designed against the first is wrong the moment it meets the second. Anything
sized by how much data there is - a strip, a grid, a chart, a list - is a
design decision made blind until the real volume is on screen.

- **Form:** our element screenshotted at the same breakpoints and themes, in a
  before / after / reference triptych in that order. Three panels, because two
  cannot show the thing that matters: whether the after actually moved. Crop
  to one instance of the element as well as showing it in place: a full panel
  beside a full reference is a picture of two panels, and whatever differs most
  in gross size - a row count, a column, how many days of history there happen
  to be - swamps the thing being matched. The wide shot gives context; the crop
  is what the question is answered on.
- **Motion:** `gif_creator` over ours, next to the reference recording. Then the
  numbers, read back out of the page with the extractor against our own
  selector - the values that shipped, not the values intended
- **Behavior:** drive it. Tab through, Enter, Escape, arrows. Check the state
  attributes flip. Check empty, long content, and error

Then the floor test, which is the one the ceiling test in `capture.md` cannot
do. Cover the labels on the triptych and ask whether a stranger would group the
after with the reference or with the before. **Grouped with the before, nothing
was matched** - whatever the measurements say. The usual cause is that the
structure never moved: same table, same columns, same order, a new radius.

The two tests are a pair and they bite from opposite sides. The ceiling asks
whether the source is nameable from ours, and fails a rebuild that took
identity. The floor asks whether ours is still what it was, and fails a rebuild
that took only detail. Reporting one and not the other is how a round passes
while changing nothing.

**Then send it, with `SendUserFile`, before saying the round is done.** The
before and the after both, in the same message, at every breakpoint and theme
the element was built at. Not described, not summarised, not left in a file
whose path is quoted back at me - sent, because I am often not at the machine
this ran on and a path is not a picture.

A round is not finished when the code is written. It is finished when I have
looked at the two images and said so. Everything before that is work in
progress, however green the checks are: the numbers say what shipped, and only
the pair of pictures says whether what shipped was worth doing.

**Expect to be wrong twice.** A sheet that matches on its first composition is
a sheet that has not been read. Compose it, sample both sides, find what is
off, fix it, compose it again - and keep going until a pass turns up nothing
new, rather than until a pass turns up something you are willing to explain
away. An account of why the two differ is not a match, and reaching for one is
the tell that the comparison stopped early. The bar is that a stranger shown
the pair would not be able to say which is which on the axis being matched.

A lane that does not match is reported as not matching -
**a lane cannot be recorded as matched because the code looks right.**

### g. Record

```bash
"$LEDGER" record --project "$PROJECT" --route "<route>" --element "<name>" \
  --status matched --matched form motion behavior \
  --reference "<the site it was matched against>" \
  --files <the files that changed>
```

`--status partial` with a short `--matched` list is the honest outcome when a
lane was left open, and it is a normal outcome. `declined` for an element I said
to leave. `inapplicable` where the element does not exist on this page after all.

Then the next element.

## 5. Closing out

Run the repo's own gates in the worktree, then commit. Write as the human
author - `ci.yml` reads every commit on the branch and refuses AI attribution or
emoji.

```bash
npm run build && npm test && git add -A && git commit -m "<what changed, in the repo's voice>"
```

Stop at the branch name. Landing is `/commit`.

Say, briefly: which elements were matched and against what, which are partial
and which lane is open on each, and which were declined. The open lanes are the
useful part - they are the list of what this page still does not do, written
down instead of forgotten.

## Notes

- **One Chrome.** Captures are serial. Do not fan out subagents at reference
  sites; they will fight over the tab and return screenshots of each other's
  pages.
- Where a reference has changed since `references/sites.md` was written, believe
  the page. That file is a set of pointers, not a set of measurements, precisely
  so it does not go stale into a wrong number.
- A round that ends with nothing built is a real round. Three references and a
  decision to keep what we have is worth more than a change nobody wanted.
- Motion and behavior are where this earns its keep. If a run is short on time,
  match those two on one element rather than form on four.
