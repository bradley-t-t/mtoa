# Capturing an element

Three lanes, because an element people call well-made is three separate
achievements and copying one of them produces a convincing still photograph of
a component that does not work.

| Lane | The question | Proved by |
| :--- | :--- | :--- |
| **Form** | What does it look like standing still | A screenshot, at every breakpoint and theme |
| **Motion** | What does it do over time, and what starts it | A recording, plus the numbers behind it |
| **Behavior** | What is it to a keyboard, a reader, a pointer | Driving it, not looking at it |

Form is the one that photographs, so it is the one that gets matched and the
other two are the ones that get eyeballed and declared close. They are the
reason the source feels better. Match all three or record the lane as open.

## Getting on the page

One Chrome, so this is serial. Load the tools in one call:

```
ToolSearch query "select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__resize_window,mcp__claude-in-chrome__gif_creator,mcp__claude-in-chrome__find"
```

Then navigate, and find the element. `read_page` gives the accessibility tree
with refs; `find` locates by text. Turn that into a CSS selector, and confirm the
selector resolves to exactly one node before capturing anything:

```js
document.querySelectorAll('SEL').length
```

Two matches means the capture is of whichever came first, which is usually not
the one on screen.

## Form

`bin/extract.js` with the selector substituted in. Run it three times and keep
all three, because a value that changes across them is a decision and a value
that does not is a constant:

1. Desktop, 1440 wide
2. Mobile, 390 wide - `resize_window` preset mobile, then reload so load-time
   gates re-run
3. The other theme, where the site has one

`sheets.blocked` in the result is the honesty check. A site serving CSS from a
CDN on another origin gives up its `:hover` rules to nobody, and the returned
`rules.states` will be short or empty. That is not a capture to write up as
complete - fall through to driving the states by hand, below.

## Motion

The resting properties tell you the contract - which properties transition, over
how long, on what curve. They do not tell you what the element does, because
nothing has happened yet.

**Drive each state and re-read.** Hover with `computer` action `hover`, then run
the extractor again and diff `form` against the resting capture. The properties
that changed are the hover state; the `transition-duration` and
`transition-timing-function` from the resting capture are how they get there.
Repeat for focus (`key` Tab onto it, which is not the same as clicking it - many
sites style `:focus-visible` and show nothing for a mouse click) and for active.

**Record the motion.** `gif_creator` over the interaction is the artifact worth
showing a person, and the one that survives into the comparison later. A curve
described in words is an opinion; the recording is the thing.

**Step the timing where it matters.** For a transition worth matching precisely,
pause it and read positions:

```js
el.getAnimations().forEach(a => { a.pause(); a.currentTime = 120 })
```

Then read `transform` off the computed style. Three or four samples across the
duration pins the curve harder than the easing keyword does, and catches the
case where the easing is a `linear()` approximation of a spring.

**Scroll-triggered reveals.** Scroll the element out of view, scroll it back,
and capture during. The `staggerHints` in the extractor catch the common
implementation - an inline `--index` on each child feeding a `transition-delay`.
Read the increment off two adjacent children; that number is the stagger.

**Check reduced motion.** Emulate `prefers-reduced-motion: reduce` and reload. A
site worth taking motion from has an answer here, and that answer is part of what
gets matched. Reproducing the animation and not the reduced-motion path ships
something worse than the source.

## Behavior

Reading it does not establish this. Drive it.

- **Keyboard.** Tab to it and through it. Note the order against
  `behavior.focusOrder`, whether focus is visible at each stop, whether focus
  gets trapped where it should be trapped and released where it should not.
  Enter and Space on the control. Escape on anything that opened. Arrows on
  anything that behaves like a list, menu or tabset.
- **State attributes.** Watch `data-state`, `aria-expanded`, `aria-selected`,
  `open` flip as you drive it. Those attribute changes are the component's real
  API, and they are what the CSS hangs off. A rebuild that animates on a class
  the framework sets at a different moment will look right once and be wrong
  under a fast click.
- **The edges.** Empty, one item, forty items, a string long enough to wrap
  twice, a slow network on anything that loads. Most of the difference between
  an element that feels finished and one that does not is in these, and none of
  them appear in a screenshot of the happy path.
- **Pointer versus keyboard.** Hover state on a touch device is a stuck hover
  unless the site handled it. Check for `@media (hover: hover)` in the captured
  rules.

## What comes across and what never does

Technique transfers. Identity does not.

**Take:** the measurements and the reasoning. Letter-spacing ramps, the leading
curve, blur radius and surface alpha, easing curves and durations, stagger
increments, grid and spacing relationships, the DOM structure that makes a
disclosure accessible, the state machine, the reduced-motion fallback, the
breakpoint the layout changes at. These are craft, they are readable by anyone
with devtools, and carrying them across is how the whole field gets better.

**Never take:** logos, wordmarks, illustration, photography, icon sets, and
licensed webfonts - all of them somebody's property and most of them somebody's
licence. Not the copy. Not the colour palette as a palette, and not the
distinctive visual signature that makes the source recognisable at a glance:
Stripe's gradient, Linear's specific dark, the one thing the site is known for.

The test is one question: could a person put the two side by side and name the
source? If yes, what came across was identity, and it goes back. That is the
legal line and it is also the design line - the point of the exercise is an
element that belongs to this project, and something wearing another company's
face fails at being that no matter how well it is built.

**And the question underneath it, which is the one that actually gets failed.**
Could that same person put the rebuild beside the thing it replaced and see any
argument at all? Taking nothing is the far more common outcome than taking too
much, because a measurement is easy to carry and a decision is not: the padding
and the curve arrive, the layout stays, and the element ends up wearing a new
radius over the same old shape. Nobody calls that stealing, and nobody calls it
matched either.

Both questions get asked out loud at the end of a round. Answering only the
first is how a round ships an unchanged element with a clean conscience.

**Fonts specifically.** Match the metrics, never the file. If the reference sets
a display step at 590 weight and -0.022em, those numbers are yours to use; the
typeface it renders in is licensed to them. Reach for the closest thing already
licensed in this project, and where the project's own face genuinely cannot do
the job, say so and let the licence be a decision rather than an accident.
