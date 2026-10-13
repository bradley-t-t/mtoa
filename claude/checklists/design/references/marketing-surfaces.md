# Marketing surfaces

What a landing page, a portfolio, or a public product page owes that an app screen does not.
Every rule here came out of repeated rounds of generated landing pages: the tells that survive a
per-file review and only show up when the page is read as a whole.

The scope matters as much as the rules. Every heading below is wrong on a dashboard. The gates
that cite this page carry `when: marketing`, which the tool reads from the target's own contents
and which `--flag marketing` or `--flag app` overrides.

## hero-fold

The hero fits the first viewport. Headline at most two lines at desktop, subtext at most twenty
words and four lines, primary CTA visible without scrolling. Copy that will not fit is copy that
has not been cut, or a font scale chosen without looking at the asset beside it. A four-line hero
headline is a font-size error, never a copy-length error.

Sensible default range is `text-4xl md:text-5xl lg:text-6xl`. `text-6xl md:text-7xl` is for a
headline of three to five words and nothing longer.

## hero-padding

Hero top padding caps at 6rem (`pt-24`) at desktop. More than that floats the content halfway down
the viewport and reads as a layout bug rather than as space. A hero that wants more room gets a
larger font scale or a larger asset, not more padding.

## hero-stack

The hero is one moment, not a feature list. At most four text elements:

1. An eyebrow, or a brand strip, or neither. Never both.
2. The headline.
3. The subtext.
4. The CTAs: one primary, at most one secondary.

Out of the hero and into their own sections below it: the tagline under the CTAs, the trust
micro-strip, the pricing teaser, the feature bullets, the social-proof avatar row.

## trust-strip

A "Used by" or "Trusted by" logo wall is a section directly below the hero, never a row inside it.
The hero carries the value proposition and the primary action.

## hero-visual

Text plus a gradient blob is a placeholder, not a hero. Even a restrained editorial page needs real
images. A pure-text page is incomplete work rather than minimalism.

## version-label

No version or status label as a hero eyebrow: `V0.6`, `v2.0`, `BETA`, `ALPHA`, `EARLY ACCESS`,
`INVITE-ONLY PREVIEW`. Acceptable only when the brief is explicitly about launch or preview status.

## eyebrow-density

An eyebrow is the small uppercase wide-tracking label above a section headline. Typical signature:
`text-[11px] uppercase tracking-[0.18em]`. Putting one above every section produces the same
templated rhythm on every page.

At most one eyebrow per three sections, hero included. A nine-section page gets three. If one
section has an eyebrow, the next two do not. The count is mechanical: instances of the uppercase
micro-label above a heading, against `ceil(sectionCount / 3)`.

What to do instead is drop it. The headline is enough, and the section's position on the page
already says what it is.

## section-numbering

No enumerated eyebrows: `00 / INDEX`, `001 · Capabilities`, `06 · how it works`, `Phase 01`. No
`01 / 4` pagination on images or tiles. No `Index of Work, 2018 - 2026` range labels. An eyebrow
names its topic in plain language or it does not exist.

## step-labels

No `Step 1 / Step 2 / Step 3`, `Stage 1 / Stage 2`, `Pass One / Pass Two`, `Phase 01 / Phase 02`.
The step content is the label. Progression is carried by the verb: Install, Configure, Ship.

## layout-repetition

A layout family appears at most once per page. Eight sections need at least four different
families. "Selected commissions" must not look like "What we do".

The zigzag has its own cap. Alternating left-image-right-text with left-text-right-image is banal
past two in a row; the third consecutive image-and-text split fails. Break it with a full-width
section, a vertical stack, a bento grid, a marquee, or any other family.

## split-header

No "left big headline, right small explainer paragraph" as a section header, and no small
paragraph floating in the top-right corner of a section head with nothing aligning to it. A section
has one focused message. When a headline needs an explainer, stack them, body under headline,
measure capped at 65ch.

Reach for a two-column header only when the right column carries a visual or an interactive
element rather than filler prose.

## micro-meta

No explanatory sentence under a section heading. "Each of these is a feature we ship today, not a
roadmap promise. The list will stay short on purpose." is the agent talking about its own work
inside the product. Eyebrow, headline, body is enough.

## marquee-count

At most one horizontal marquee per page.

## bento-cells

A bento grid has exactly as many cells as it has content for. Three items is three cells, five
items is five. An empty cell in the middle or at the end means the grid was shaped before the
content was counted. Reshape it rather than pasting a blank tile.

Cells also need visual variation. Six white-on-white cards with only type inside reads as a
default even when the rest of the page is good; at least two or three cells in any multi-cell grid
carry a real image, a brand-appropriate gradient, a pattern, or a tinted surface.

## long-lists

Past five items a list changes component. The default `<ul>` with bullets, or `divide-y` rows with
a hairline under every one, is the laziest available layout and the one that ships most.
Alternatives, in rough order of reach: grouped two-column split, card grid with an image per item,
tabs or accordion when the items are categorisable, horizontal scroll-snap pills, a carousel for
breadth, a marquee for things that need no individual attention.

Spec sheets specifically: group ten rows into three labelled clusters with one soft divider each,
or give each spec a card with the value set large and a one-line reason it matters, or feature
three or four as display tiles and collapse the rest behind a disclosure.

## comparison-bars

No scoring or progress bars with filled background tracks as comparison visuals. A number with a
small icon, or a thin inline bar with no track, carries the same information without importing
dashboard chrome onto a marketing page.

## scroll-cues

No `Scroll`, `↓ scroll`, `Scroll to explore`, `Scroll to walk through it`, no animated mouse-wheel
icon. A reader who has not scrolled yet is looking at the hero and knows what scrolling is.

## decoration-strips

No mono-caps strip across the bottom of the hero: `BRAND. MOTION. SPATIAL.`, `TYPE / FORM /
MOTION`, `ESTD. 2018 · LISBON`. Acceptable only when the strip carries real navigation or real
status.

No locale, time, or weather strip: `Lisbon 14:23 · 18°C`, `LIS 14:23`, `1200-690 Lisbon,
Portugal` as atmosphere. A contact address in the footer is fine. Allowed as decoration only for a
studio that really is spread across timezones, a travel brand, or a real physical venue.

No vertical rotated text as decoration. No crosshair or hairline grid drawn only to make the page
feel designed; lines are for organising real content.

## separators

The middle dot is rationed to one per line in a metadata strip. It is not the default separator
for everything: `foo · bar · baz · qux` is a line that wanted columns, hairlines, or a line break.

Zero decorative status dots. A coloured dot before a nav link, a list row, or a badge is
decoration unless it carries real semantic state, and then it appears once per section at most.

## image-labels

No pill, tag, or label overlaid on an image: `Brand · 02`, `PLATE · BRAND`, `Field notes -
journal`. Either the image speaks alone or a caption sits below it, outside the frame.

No photo-credit caption as decoration: `Field study no. 12 · Ines Caetano`, `Frame XII · 35mm`.
Credit belongs to a real photographer of a real photograph. Otherwise the caption is functional or
absent.

## version-footer

No `v1.4.2`, `Build 0048`, `last sync 4s ago · main` in a marketing footer. Those are devtool
fixtures. Nor a live-stock counter (`Reservation 412 of 800`) unless the number is real.

## custom-cursor

No custom mouse cursor. Outdated, hostile to accessibility, hostile to frame rate.

## accent-lock

One accent for the whole page. A warm-grey site does not get a blue CTA in section seven; a
rose-accented site does not get a teal badge in the footer. Pick the accent, lock it, check every
component before shipping.

Saturation stays under 80% by default, on a neutral base. The automatic purple-blue glow is a
default rather than a decision; when the brief does ask for violet, execute it with a harmonised
palette and restrained gradients.

## theme-lock

The page holds one theme. A light warm-paper section between dark sections tells the reader they
walked into a different website. Background tints within one family are fine (`bg-zinc-950` beside
`bg-zinc-900`); flipping to `bg-amber-50` in the middle of a dark page is broken.

A deliberate colour-block story or a scroll-driven theme switch is allowed once per page when the
brief asks for it, as one composed transition rather than alternation.

## serif-discipline

Serif is not the default for a creative brief. "It feels premium" is not a reason, and the
reflex that a creative brief means a serif is the most-tested tell in production rounds.

Serif is justified when the brief names one, or when the work really is editorial, luxury,
publication, manuscript, heritage, or vintage and the choice of that specific face can be
articulated for that specific brand. Everything else defaults to a sans display face.

`Fraunces` and `Instrument Serif` are banned as defaults outright, being the two the model reaches
for first. When a serif is justified, rotate rather than reusing the last one.

Emphasis inside a headline uses italic or bold of the same family. Injecting a serif word into a
sans headline for visual interest is amateur.

## premium-consumer-palette

For premium-consumer briefs (cookware, wellness, artisan, luxury, heritage craft, DTC home goods)
the reflex palette is warm beige or cream, brass or clay or oxblood or ochre, espresso near-black
text. Concretely, as defaults: backgrounds `#f5f1ea`, `#f7f5f1`, `#fbf8f1`, `#efeae0`, `#ece6db`,
`#faf7f1`, `#e8dfcb`; accents `#b08947`, `#b6553a`, `#9a2436`, `#9c6e2a`, `#bc7c3a`, `#7d5621`;
text `#1a1714`, `#1a1814`, `#1b1814`.

Every premium-consumer page built this way looks like every other one, and the brand disappears.
Rotate instead: cold luxury (silver, chrome, smoke), forest (deep green, bone, amber), black and
tan, cobalt and cream, terracotta and slate, olive and brick and paper, or monochrome with one
saturated accent.

Allowed when the brand brief names those colours, or when the identity really is warm-craft and
the fit can be argued for that brand.

## glow-and-gradient-text

No neon or outer glow by default; an inner border or a tinted shadow does the work. Shadows are
tinted to the background hue, never pure black on a light surface. No gradient fill on display
type.

## em-dash

Zero em-dashes in any string the reader sees: headlines, eyebrows, pills, button labels, body
copy, quotes, attribution, captions, alt text, nav items. The en-dash used as a separator goes
with it; ranges take a hyphen.

The rule is binary because every softer phrasing of it has been ignored. Restructure the sentence:
a period, a comma, parentheses, or a colon. The only dash characters that ship are the hyphen and
the minus sign.

This gate covers visible UI strings. Prose in a README or a commit message answers to
`writing-pass.py`, which reports em-dash density without blocking, and that stays as it is.

## cta-intent

One label per intent, everywhere on the page. "Get in touch", "Contact us", "Let's talk", "Start a
project", "Reach out" are one intent and one label. Same for "Try free" and "Get started" and "Sign
up free"; same for "View work" and "See selected work" and "Browse projects".

Primary CTA copy is three words at most, ideally one or two, and never wraps to a second line at
desktop. A label that wraps is either too long or in a button someone constrained.

## copy-density

Per section: headline of eight words or fewer, sub-paragraph of twenty-five words or fewer, and one
visual or one CTA. Anything more is justified by that section's job or cut.

No data-dump sections. A twenty-row publication table or a thirty-row award list on a marketing
page is the wrong layout, not a long one.

Numbers are real, labelled as mock, or gone. `92%`, `4.1×`, `5.8 mm` invented for the look of
engineering precision claim something the brand does not.

One copy register per page. Technical mono, editorial prose, and marketing punch in one composition
read as three writers unless the brand voice actually calls for it.

Re-read every visible string before shipping: headlines, subheads, eyebrows, button labels, body,
captions, alt text, footer, errors. Anything grammatically broken, referentially unclear, or
mock-poetic gets replaced with a plain functional sentence. Boring copy beats cute wrong copy.

## quotes

Quote body at most three lines. A landing-page quote is a snippet; a longer original gets cut.
Attribution carries name and role, optionally company, never a bare first name. Real typographic
quote marks or none.

## imagery

Priority order for visual assets:

1. An image-generation tool, when one is available in the environment, producing section-specific
   assets at the right aspect ratio.
2. Real photography. `https://picsum.photos/seed/{descriptive-seed}/{w}/{h}` with a seed that
   describes the section, actual brand assets when the brief provides them, or an open-license
   source when one is allowed. A bare `source.unsplash.com` URL is a broken image waiting to
   happen.
3. Labelled placeholder slots plus an explicit list of what the page needs, handed back with the
   work.

What does not count: hand-rolled decorative SVG, and a product preview built out of styled `<div>`
rectangles. A fake dashboard, fake task list, or fake terminal is the most reliable tell there is.
Use a real screenshot, a generated image, a real miniature of the component, or nothing.

## logo-walls

A logo wall uses real marks: Simple Icons (`https://cdn.simpleicons.org/{slug}/{hex}`), devicon for
tech stacks, or a generated monogram for an invented brand. Styled text wordmarks in a row read as
placeholder.

The wall carries logos and nothing else. No industry or category label under each mark: not
`Vercel` over `hosting`, not `Stripe` over `payments`. The logo is the credibility. Brand name as
alt text, optionally a link, and that is the whole component.

## icons

One icon family per project, standard stroke width, never hand-drawn paths. A missing glyph means
a second library or a composition from primitives.

## scroll-and-state

`window.addEventListener("scroll", ...)` is banned. It runs every frame, batches nothing, and
janks. The alternatives are Motion's `useScroll()`, GSAP's `ScrollTrigger`, `IntersectionObserver`,
or CSS scroll-driven animation (`animation-timeline: view()`).

Continuous values driven by input (pointer position, scroll progress, magnetic hover) never live in
React state. `useState` re-renders the tree on every change and collapses on mobile; motion values
(`useMotionValue`, `useTransform`) exist for this. A `requestAnimationFrame` loop that sets state
is the same defect wearing a different hat.

Every animation effect cleans up. Motion lives in an isolated `'use client'` leaf, not smeared
across a server tree.

## page-mechanics

Grain and noise filters go on fixed, `pointer-events-none` overlays. On a scrolling container they
repaint continuously and destroy frame rate on mobile.

Grid rather than flex percentage math: `grid grid-cols-1 md:grid-cols-3 gap-6`, never
`w-[calc(33%-1rem)]`.

Fonts are self-hosted or loaded through the framework's font pipeline. A Google Fonts `<link>` in
production blocks render on a third-party host.

## dependencies

Every imported package exists in `package.json` before it is imported. A hallucinated import is the
most common way a generated page fails to build at all.

One design system per project. Material beside shadcn is two systems arguing. A system's components
are customised to the project rather than shipped in their default state.

## nav-shape

The navigation renders on one line at desktop. Items that do not fit at 1024px get shorter labels,
fewer secondary entries, or a disclosure. Two lines at desktop is broken.

Height caps at 80px, default 64 to 72. A bar eating a sixth of the viewport is an agency habit, not
a decision.

## design-read

Before the first file, the brief is read and the reading is recorded: page kind, audience, vibe
words the brief used, references it named, brand assets that already exist, and any quiet
constraint that outranks aesthetics (accessibility-first audience, public sector, regulated
industry, trust-first commerce, children).

The audience picks the aesthetic. A model that skips this step jumps to a default one, which is
where most bad generated design comes from.

## rotation

The direction differs from the last run of the same genre. Two premium-consumer pages in a row do
not share a palette family; two editorial pages do not share a serif. The reflex is invisible from
inside one project and obvious across three.
