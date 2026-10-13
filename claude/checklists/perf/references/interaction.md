# Responsiveness

Interaction to Next Paint measures the worst delay between a reader acting and the
screen answering, across the whole visit. It is the metric a loading-focused pass leaves
untouched and the one most likely to be rated poor, because it is paid on every tap
rather than once at the start.

A page can load in under two seconds and be rated SLOW on INP alone. When the field
record says that, image work and bundle splitting will not move the rating: the problem
is what occupies the main thread when somebody touches the page.

## What the measurement contains

Three parts, and the fix is different for each:

1. **Input delay** - the thread was busy when the tap arrived. Caused by whatever else
   was running: a framework rendering, a third-party script, an animation frame.
2. **Processing time** - the handler itself ran long.
3. **Presentation delay** - the handler finished but the browser could not paint,
   usually because the resulting render was large.

The measurement ends at the next paint. Painting the immediate feedback first and doing
the rest of the work afterwards addresses all three at once.

## Yield before working

```js
async function onFilterChange(value) {
  setActiveFilter(value);              // paint the pressed state
  await new Promise(r => setTimeout(r, 0));  // let the browser paint it
  setResults(expensiveFilter(value));  // then the expensive part
}
```

In React, `startTransition` marks the second update as interruptible, which does the
same thing with less ceremony. `scheduler.yield()` is the direct form where it is
available.

Filtering, sorting, parsing and network calls all belong after the yield.

## Animation on the compositor

`transform` and `opacity` are the only two properties the browser can animate without
laying the page out again. Everything else - `width`, `height`, `top`, `left`, `margin`,
`padding`, `font-size`, `box-shadow` - forces layout on every frame, on the same thread
that has to answer the next tap.

Every motion has a compositor form that looks identical:

| Instead of | Use |
| --- | --- |
| `left: 0 -> 40px` | `transform: translateX(40px)` |
| `width: 100px -> 140px` | `transform: scaleX(1.4)` with a transform-origin |
| `top` on scroll | `transform: translateY()` |
| `box-shadow` spread on hover | a pseudo-element whose `opacity` animates |

`will-change: transform` promotes the element to its own layer, which helps and costs
memory. It goes on the few things that animate continuously, not on everything.

## Loops that never stop

A WebGL field, a particle canvas, a parallax handler or a marquee that keeps running
behind the footer competes with every interaction for the length of the visit. Three
conditions end that without ending the effect:

```js
const io = new IntersectionObserver(([entry]) => {
  entry.isIntersecting ? start() : stop();
});
io.observe(canvas);

document.addEventListener("visibilitychange", () => {
  document.hidden ? stop() : start();
});

if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
```

Off-screen, hidden tab, and reduced motion. The effect looks the same to anyone looking
at it.

## Passive listeners

A non-passive `scroll`, `wheel` or `touchmove` listener makes the browser wait for the
handler before it may scroll, because the handler might call `preventDefault`. Declaring
that it will not removes the wait:

```js
window.addEventListener("scroll", onScroll, { passive: true });
```

Where the handler measures the page, an `IntersectionObserver` or `ResizeObserver` does
the same work off the main thread.

## Layout thrash

Reading a measured property forces the browser to flush pending layout. Reading and
writing alternately in a loop forces one full layout per iteration:

```js
for (const card of cards) {
  const height = card.offsetHeight;   // read: forces layout
  card.style.height = height + "px";  // write: invalidates it again
}
```

Reading everything first and then writing turns N layouts into one. The properties that
force it: `offsetWidth`, `offsetHeight`, `offsetTop`, `clientWidth`, `clientHeight`,
`scrollTop`, `scrollHeight`, `getBoundingClientRect`, `getComputedStyle`.

## Long lists

Every row that exists is style, layout and memory the browser carries through every
interaction on the page. A list several viewports long is windowed, paginated, or given
`content-visibility: auto` on its rows.

## Skipping what nobody has reached

```css
.section {
  content-visibility: auto;
  contain-intrinsic-size: auto 600px;
}
```

The browser skips style, layout and paint for the section until it is approached. The
intrinsic size is what keeps the scrollbar honest while it is skipped - without it, the
page height changes as sections are reached, which is layout shift caused by a
performance fix.

## Third-party code

A tag manager and a chat widget are each capable of holding the thread for longer than
the entire interaction budget, and neither is under this project's control. Loading them
after the load event, on an interaction, or in a worker via Partytown are the options.
Where one must run inline, its cost is measured and recorded rather than assumed.
