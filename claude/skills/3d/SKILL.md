---
name: 3d
description: Build a section where a subject cut out of a photograph stands in real depth - occlusion against a frame that holds still, scroll drift and a pointer turn on the subject alone, in the project's own tokens. Use on /3d, or "make this look 3d", "put my photo in the page like it's standing there", "layered parallax section", "text behind the image", "make this section have depth".
argument-hint: "<the photo> [the page or section it goes in]"
---

# 3d

Take a photograph, cut the subject out of it, and stand it in a band that has
actual depth: a frame the subject breaks and does not move, and the subject
answering the scroll and the pointer against it.

The look people point at - the snowboarder standing in the middle of the word
behind him - is not one effect. It is three, and the first is worth more than
the other two together.

| | What it is | What it costs |
| :--- | :--- | :--- |
| **Occlusion** | The subject in front of one thing and behind another | Nothing. It is stacking order |
| **Parallax** | The subject travelling against a frame that does not | A scroll reading and a spring |
| **Perspective** | A real `rotateY` on the subject under its own `perspective()` | One transform written per frame |

Occlusion is the whole read. A reader knows a thing is in front because it hides
what is behind it, and that is true in a still screenshot with no JavaScript
running at all. Parallax and perspective make it move; they do not make it read.
So the composition is settled first and the motion is added to a band that
already works standing still.

## The frame holds still

**One thing in the band moves, and it is the subject.** The panel, the copy and
any foreground caption are static.

This is the single decision that separates a band with depth from a band that
slides. Relative motion needs something to be relative to: move the frame as
well and there is no fixed thing left, so nothing is measured against anything
and the layers read as coming apart rather than as standing at different
distances. Hold the frame and move one object inside it and the same few pixels
read as depth immediately.

The frame is also where the words are, and a paragraph that drifts while it is
being read is a paragraph the reader is fighting. No amount of depth pays for
that.

## What this is not

Not WebGL. No Three.js, no Spline, no canvas. Everything here is `transform` on
composited layers, which is why it costs nothing on a phone and survives a
Lighthouse run. If the job genuinely needs geometry, lighting and a camera, this
is the wrong skill and it should be said so plainly rather than approximated.

## The order of work

### 1. Look at the photograph first

Almost every problem in this kind of band is decided before any code is written,
by the crop the photographer chose.

Open the image and answer one question: **where does the subject run into the
edge of its own frame?** A studio portrait is cropped tight, so the shoulders
reach the left and right edges somewhere around two thirds down. Everything
below that line is a straight vertical cut with no pixels beyond it, and no
amount of positioning will make it read as a free-standing figure.

Two honest ways past it, and one that is not:

- **Feather the touched edges** so the subject dissolves into the ground rather
  than ending on a crop. This is what `bin/cutout.py` does, and it is the
  default.
- **Put something in front of it** — a card, a rail, the band's own foreground
  plane — covering the region where the cut happens.
- Not: cropping above the contact point. On a head-and-shoulders portrait that
  leaves a floating head. Try it once, look at it, and stop.

### 2. Cut it out

```bash
~/.claude/skills/3d/bin/cutout.py <photo> --out <project>/public/images --name <subject>-figure
```

Removes the background with `rembg` (alpha matting on, so hair stays hair),
detects which frame edges the silhouette actually reaches, feathers only those,
and writes two WebP widths plus previews composited on a light and a dark
ground. It prints the `srcSet` to paste.

**Look at both previews before going further.** A halo invisible on paper is a
grey fringe on a dark slab, and the site draws on both. If the edges are wrong,
that is the moment to fix them - not after the band is built around them.

Budget: a 900px-wide portrait lands around 55–60KB at quality 80. Serve a narrow
variant, `loading="lazy"` unless it is above the fold, explicit `width`/`height`,
and real `sizes`.

### 3. Decide what the subject stands on and what stands in front of it

Three things, and each one has to be something the section needed anyway.
Decoration invented to carry the effect is what makes these bands look like a
template.

| | Usually | Moves |
| :--- | :--- | :--- |
| **Behind** | The panel, card or surface carrying the copy — the frame the subject breaks | No |
| **The subject** | The cutout | Yes, and it is the only one |
| **In front** | One small real element crossing the subject: a caption, a name plate, a figure the page already publishes | No |

The two occlusions are: the subject over the frame's surface and past its top
edge, and the front element across the subject's body. Both are pure geometry,
so both are guaranteed rather than tuned — and both survive with every animation
switched off, which is the test of whether the composition is doing the work.

Read `references/deck.md` before writing any of it. It carries the nesting, both
motion sources, and the seven ways this breaks silently.

### 4. Build it in the project's own language

Every value comes from the project: its ground and surface tokens, its type
scale, its easing curves, its container, its motion constants. A band built in
generic Tailwind greys next to a designed page reads as a widget dropped in.
Read a neighbouring section first and match how it is written, comments
included.

Copy is the project's own too, and preferably imported from wherever it already
lives rather than retyped. A band on the front door quietly disagreeing with the
page it links to is worse than no band.

### 5. Prove it at both ends

Motion is not the check. The check is the still composition at 375 and at 1440,
plus:

- No horizontal scrollbar at 375. `scrollWidth === clientWidth`.
- The subject is not clipped by its own band at any scroll position — the drift
  moves it, so check with the drift at both extremes.
- The subject is centred where it should be centred. This is the one that
  silently fails; see the note about motion values and `transform` in
  `references/deck.md`.
- The frame really is fixed. Read the panel's and the caption's coordinates at
  two opposite pointer positions and at two scroll offsets: four readings, one
  number each, and a single pixel of movement is a bug.
- Both light and dark, if the project has both.
- `prefers-reduced-motion: reduce` holds everything still and the band still
  reads, because occlusion is doing the work.

`references/deck.md` ends with a DOM probe that answers all of these at once and
does not depend on a screenshot. Prefer it: preview panes go stale when hidden,
and `requestAnimationFrame` does not run in a backgrounded or occluded browser,
so a pointer test there writes nothing and reads as a dead effect. Taking a
screenshot forces a frame and runs the pending callback, which is the way to
measure the pointer response without the window in front.

## The restraint

Depth is a claim about what matters. A band where everything moves is a band
with no subject.

One mover. A turn of a couple of degrees, not fifteen. Travel a reader feels
rather than watches. The moment somebody can name the effect, it has stopped
being depth and started being a trick, and a trick dates the page within a
year.
