# Images, fonts and embeds

Images are usually the heaviest thing a page sends and the easiest to send wrongly.
Every rule here changes which file a device receives, never which picture it shows.

## One file for every device

A 2000px hero served to a 390 point phone is roughly four times the bytes needed to draw
the same picture, and the excess is decoded on the main thread and thrown away.

```html
<img
  src="/images/hero-1200.webp"
  srcset="/images/hero-480.webp 480w,
          /images/hero-800.webp 800w,
          /images/hero-1200.webp 1200w,
          /images/hero-2000.webp 2000w"
  sizes="(max-width: 768px) 100vw, 60vw"
  width="1200" height="800"
  alt="..." />
```

`sizes` describes the layout, not the file: it tells the browser how wide the element
will be before any CSS has loaded, so it can choose correctly on the first pass. Getting
it wrong is the usual reason a correct `srcset` still ships the largest file.

Frameworks with an image component - `next/image`, `astro:assets`, `@nuxt/image` -
generate all of this. Where one is in use, the gate is satisfied by using it.

## Format

AVIF is roughly half of WebP, which is roughly a third of JPEG, at visually equivalent
quality. Offering all three costs nothing but build time:

```html
<picture>
  <source srcset="/images/hero.avif" type="image/avif" />
  <source srcset="/images/hero.webp" type="image/webp" />
  <img src="/images/hero.jpg" width="1200" height="800" alt="..." />
</picture>
```

Photographs go to AVIF or WebP. Flat graphics, logos and icons go to SVG. Screenshots
with text stay lossless. Icons and favicons are neither photographs nor first-screen
weight and are outside this.

Lowering quality to save bytes is a design change and is refused. Format and dimensions
are where the bytes are.

## Loading order

Three attributes decide when an image is fetched, and getting them backwards is common:

- **The hero**: no `loading` attribute, `fetchpriority="high"`, preloaded in the head.
  Lazy-loading the element the browser will measure as the largest paint delays the
  metric by exactly the time it takes to discover it.
- **Everything below the fold**: `loading="lazy"`. It competes with the first screen for
  bandwidth otherwise, and the reader may never reach it.
- **`decoding="async"`** on anything not on the first screen, so decoding does not block
  the main thread.

The dividing line is the fold at the narrowest supported width, where more of the page
is below it.

## Reserving space

An image with no dimensions has no size until its bytes arrive, so the page reflows as
each one lands. That is CLS, and to a reader it is the page jumping while they read.

`width` and `height` attributes carrying the file's real pixel dimensions are enough:
the browser derives the aspect ratio from them and reserves the box, and CSS still
controls the displayed size. `aspect-ratio` in CSS does the same for a background or a
container.

## Embeds

A YouTube iframe loads a player, its styles, its analytics and its recommendation logic
- frequently more than the rest of the page combined - and most readers never press
play. A facade shows the thumbnail with the play control drawn on it and mounts the real
iframe on click. The reader sees the same thing; the page does not pay for it until they
want it.

The same applies to maps, Spotify and SoundCloud players, Twitter and Instagram embeds,
and chat widgets.

## Fonts

Covered in full in `critical-path.md`. The short version: self-host, subset, set
`font-display: swap`, preload only the faces the first screen sets, and declare a
metric-matched fallback so the swap does not reflow the text it was standing in for.
