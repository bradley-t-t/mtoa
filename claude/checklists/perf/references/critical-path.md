# The critical path

First paint happens when the browser has everything it needs to draw and nothing left
blocking it. Everything between the document arriving and that moment is the critical
path, and on a client-rendered site it is usually the entire application.

## What the browser is waiting for

1. **The document.** Every redirect in front of it is a full round trip on a cold
   mobile connection. TTFB counts from the first request, so a redirect is inside every
   later metric.
2. **Render-blocking CSS.** All of it, parsed, before anything paints. Including the
   rules for routes nobody is on.
3. **Blocking script.** A `<script src>` in the head with no `defer`, `async` or
   `type="module"` stops parsing until it has been fetched and run.
4. **On a single-page app: the bundle.** Download, parse, execute, render. An empty
   root element means first paint cannot happen before all four have finished.

## The empty shell

```html
<body>
  <div id="root"></div>
  <script src="/assets/index-CXMKMWL_.js" type="module"></script>
</body>
```

That document contains no content. On a mid-range phone over 4G, a 700KB bundle costs
roughly a second to download, most of another to parse and compile, and more again to
build the tree and lay it out. Three seconds to first paint is normal, and the largest
element usually lands seconds after that, because the image it references cannot even
be discovered until the component naming it has mounted.

Nothing else in this file matters as much. A preload saves a round trip; prerendering
saves the whole chain.

**Prerendering** runs the app at build time and writes the resulting markup into the
HTML. The client bundle still loads and takes over, so behaviour is unchanged - the
reader simply sees the page while that happens rather than after it. In Vite this is
`vite-plugin-prerender` or a `vite-ssg` setup; in Next, Nuxt, Astro and SvelteKit it is
what the framework does by default when a route is not marked dynamic.

For a marketing site, every route is prerenderable. For an app behind a login, the shell
and the first screen are, which is where the metric is measured.

## Stylesheets

- One stylesheet for the whole site is parsed in full before the first paint of every
  route. Splitting it along the same lines as the routes, or inlining what the first
  screen needs and loading the rest without blocking, cuts what the paint waits for.
- `@import` inside CSS is a serial round trip: the browser cannot discover the second
  file until it has parsed the first. A `<link>` in the document, or a bundler inlining
  it at build time, removes the wait.
- Tailwind's output is mostly unused per route but compresses extremely well and is
  usually not the problem. Measure before splitting it.

## Fonts

A web font is invisible text until it arrives. `font-display: swap` renders the fallback
immediately and swaps when the file lands, which is almost always right - the alternative
is a reader looking at nothing.

The swap itself causes reflow unless the fallback occupies the same space. Metric
overrides fix that without changing the face:

```css
@font-face {
  font-family: "Barlow Fallback";
  src: local("Helvetica Neue"), local("Arial");
  size-adjust: 105.2%;
  ascent-override: 92%;
  descent-override: 24%;
}
```

Then `font-family: Barlow, "Barlow Fallback", sans-serif`. The numbers come from
comparing the real face's metrics to the fallback's; `fontpie` and `capsize` compute
them.

Beyond that: self-host rather than using a third-party font host, which adds a cold
origin to the critical path for a file that could have arrived with the page; subset to
the characters in use, which typically takes a 200KB face under 30KB; preload only the
faces the first screen actually sets, because a preload for an unused weight competes
with the ones that matter.

## The largest element

LCP measures whichever element is largest in the viewport when painting settles -
usually a hero image, sometimes a heading.

The browser cannot start fetching what it has not found. Three things put it early:

- The element is referenced by the **document**, not built by script that runs later.
- It is **preloaded** in the head.
- It carries **`fetchpriority="high"`**, so it does not queue behind everything else the
  parser found first.

A CSS background image fails all three. It is discovered only once the stylesheet naming
it has parsed, and there is no way to give it a priority. Where the hero is a background
for layout reasons, an `img` with `position: absolute; inset: 0; object-fit: cover`
renders identically and is discoverable.

## Third parties

Every external origin costs DNS, TCP and TLS before the first byte of whatever it was
fetching. `rel="preconnect"` opens that connection early, and is worth spending on the
two or three origins the first screen cannot draw without; `rel="dns-prefetch"` is the cheap
version for the rest.

A third-party script with no `defer` or `async` hands a stranger control of when the
page appears. Analytics, tag managers, chat widgets and pixels all belong after the load
event or behind an interaction.
