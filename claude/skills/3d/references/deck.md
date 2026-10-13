# The deck

The markup and the two motion sources, in the order they have to be nested. The
class names below are placeholders — every one of them gets replaced with the
project's own before this is written to a file.

## One mover

The frame holds still. The subject moves. That is the rule, and it is not a
matter of taste.

Depth is relative motion, and relative motion needs something to be relative
to. A band where the panel drifts one way, the subject another and the caption a
third has no fixed thing in it, so nothing is measured against anything and the
whole band reads as sliding apart. Hold the frame and move one object inside it
and the same few pixels read as depth immediately.

It is also where the words are. A paragraph that drifts while it is being read
is a paragraph the reader is fighting, and no amount of depth pays for that.

So: the back plane, the copy and any foreground caption are static. The subject
answers the scroll and the pointer. Nothing else moves.

## Nesting

The order is not stylistic. Each level exists because the one inside it cannot
do its own job otherwise.

```
section          the band; the scroll target; clips across, not down
  stage          `relative`; the positioning context the layers size to
    panel        the frame, in flow, giving the stage its height — no transform
    layer        `absolute inset-0`, over the panel
      drift      the motion value's `y` lives here and nowhere else
        lean     the pointer transform is written here by hand
          img     placement lives in ordinary classes here
    plate        a second `absolute inset-0` layer, in front, also static
```

Three elements around the image and one transform each. They cannot be folded
together: the drift is a motion value and writes the whole `transform`, the lean
is written by hand and would be overwritten by it, and the placement is a class
and would be overwritten by either. Folded onto one element they agree at
whatever width the work was done at and nowhere else.

## The turn

`perspective` as a *property* reaches an element's children and nothing deeper,
and the subject sits three levels down. Put it in the subject's own transform as
the `perspective()` *function* instead — then no ancestor can lose it, and
nothing needs `transform-style: preserve-3d` at all.

```js
const PERSPECTIVE = 1400

const LEAN_X = 16 // pixels at the left and right edges of the band
const LEAN_Y = 10 // pixels at the top and bottom
const TURN = 2.2 // degrees of rotateY at the left and right edges
```

The lean carries the depth, because it is the subject moving against a frame
that is not. The turn is the smaller of the two on purpose: it is a face, and a
face rotated far enough to notice is a photograph warping rather than a person
looking up. Two degrees is plenty. Six is a novelty.

```jsx
useEffect(() => {
  const lean = leanRef.current
  const stage = sectionRef.current
  if (!lean || !stage) return undefined

  const settle = () => {
    lean.style.transform = ''
  }

  const fine = window.matchMedia('(hover: hover) and (pointer: fine)')
  if (reduced || !fine.matches) {
    settle()
    return settle
  }

  let frame = 0
  let next = null

  const apply = () => {
    frame = 0
    lean.style.transform =
      `perspective(${PERSPECTIVE}px) ` +
      `translate3d(${next.x}px, ${next.y}px, 0) rotateY(${next.turn}deg)`
  }

  const onMove = event => {
    const { left, top, width, height } = stage.getBoundingClientRect()
    if (width === 0 || height === 0) return
    const across = (event.clientX - left) / width - 0.5
    const down = (event.clientY - top) / height - 0.5
    next = { x: -across * LEAN_X, y: -down * LEAN_Y, turn: across * TURN }
    if (!frame) frame = requestAnimationFrame(apply)
  }

  const onLeave = () => {
    next = { x: 0, y: 0, turn: 0 }
    if (!frame) frame = requestAnimationFrame(apply)
  }

  stage.addEventListener('pointermove', onMove, { passive: true })
  stage.addEventListener('pointerleave', onLeave, { passive: true })
  return () => {
    stage.removeEventListener('pointermove', onMove)
    stage.removeEventListener('pointerleave', onLeave)
    if (frame) cancelAnimationFrame(frame)
    settle()
  }
}, [reduced])
```

Written straight to the node inside one `requestAnimationFrame`, never through
state: the pointer moves every frame and a render per frame rebuilds the band
under itself. Signs are a decision — leaning *away* from the pointer reads as
the subject standing behind the glass, leaning *towards* it as the subject
leaning out. A CSS `transition` on the lean element (250–350ms, the project's
own ease-out) keeps the movement from snapping between samples.

## The drift

```jsx
const { scrollYProgress } = useScroll({
  target: sectionRef,
  offset: ['start end', 'end start'],
})

const figureY = useSpring(
  useTransform(scrollYProgress, [0, 1], reduced ? [0, 0] : [DRIFT, -DRIFT]),
  { stiffness: 120, damping: 30, mass: 0.5 }
)
```

`DRIFT` around 30 pixels. It is the only travel in the band, so it can be a
little more generous than it would be if the frame were moving too — but a
subject a reader can watch travelling has stopped being depth.

## Where it breaks

**A motion value writes the whole `transform`.** `style={{ y }}` beside
`-translate-x-1/2` in a class produces an element that is not centred, at every
width, with no error anywhere. This is the one that will be silently wrong on
phones while looking perfect on the desktop the work was done on.

**`perspective` as a property only reaches direct children.** Set on the stage
while the subject is three levels down, the `rotateY` renders orthographically —
a horizontal squash instead of a turn, which reads as the image glitching. Use
the `perspective()` transform function on the element that turns.

**`overflow: hidden` on the band cuts the head off.** The subject leaving the
frame is the entire effect. Clip across only — `overflow-x: clip` with
`overflow-y: visible` — so the lean cannot hand a phone a horizontal scrollbar
while the top stays open. Check `scrollWidth === clientWidth` at 375 before
believing it.

**A panel with its own `overflow: hidden` cannot be the subject's parent.** Lay
the subject over it as a sibling, or the frame it is supposed to break will
quietly contain it.

**The dissolving foot must land on flat surface.** A feathered edge crossing the
frame's own border shows the border through the subject. Anchor the foot well
inside the panel.

**`requestAnimationFrame` does not run in a hidden tab.** A pointer test in an
occluded or backgrounded browser writes nothing and reads as a dead effect.
Check `document.visibilityState` before concluding anything is broken — and see
the note below for the way around it.

**Reduced motion and coarse pointers.** Collapse the drift range to `[0, 0]` and
never attach the pointer listener. Both gates, every time.

## Proving it, without eyes on it

Screenshots go stale in a hidden preview pane, and `requestAnimationFrame` is
frozen there too — so a hand-written pointer transform never lands and the
effect looks dead when it is fine. **Taking a screenshot forces a frame**, which
runs the pending callback. Sandwich one between the dispatch and the read:

1. dispatch `new PointerEvent('pointermove', { clientX, clientY, bubbles: true })`
   on the **section** (the node the listener is on, not a wrapper around it)
2. take a screenshot — any scale, `0.1` is enough and costs almost nothing
3. read the transform back

Do that at two opposite corners. The subject's transform differs; the frame's
numbers are identical. For the drift, read positions in document space
(`getBoundingClientRect().y + scrollY`) at two scroll offsets — the frame's is
constant, the subject's is not.

```js
const sec = document.querySelector('<the band>')
const panel = sec.querySelector('<the frame>')
const sub = sec.querySelector('img')
const front = sec.querySelector('<the front plate>')
const r = e => e.getBoundingClientRect()

;({
  hScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  subjectCentred: Math.abs((r(sub).left + r(sub).right) / 2 - (r(panel).left + r(panel).right) / 2) < 3,
  breaksTopEdge: Math.round(r(panel).top - r(sub).top),
  notClippedByBand: r(sub).top - r(sec).top,
  frontCrossesSubject:
    r(front).left < r(sub).right &&
    r(front).right > r(sub).left &&
    r(front).top < r(sub).bottom &&
    r(front).bottom > r(sub).top,
  footInsidePlane: Math.round(r(panel).bottom - r(sub).bottom),
  frameX: [r(panel).x, r(front).x], // must be identical at every pointer position
})
```

`hScroll` is 0. `subjectCentred` is true wherever the subject is meant to be
centred. `breaksTopEdge` is positive and large enough to see. `notClippedByBand`
may be negative — that is the head standing proud of the band, and it is only
safe once the band clips across and not down. `frontCrossesSubject` is true, and
it has to cross *opaque* pixels rather than the feathered tail.
