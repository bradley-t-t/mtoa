# Tables, headers, and the space between things

Three areas a generated interface gets close to right and stops. A table looks like
a table long before it is scannable, a header looks like a header long before it
orients anyone, and spacing passes every value check while the arrangement is still
wrong.

The TBL, HDR, and SPC gates check what follows. Smyrna Tools' asset list is the
working reference for the table and header patterns here.

## Tables

A table is scanned, not read, and everything below serves the scan.

**Rows stay dense and single-line.** A fleet list runs to hundreds of rows, and
anything taller than it needs turns scanning into scrolling. Padding, type size,
and any badge inside the row are tuned together — grow one and the row height goes
with it. Set a row height token and size the contents against it.

**The row answers the pointer, through its cells.**

```jsx
<tr className="cursor-pointer hover:[&>td]:bg-bg-tertiary border-b border-border-light">
```

Hovering the `<tr>` and setting a background on it does nothing where the cells
have their own background — they paint over it and the hover half-applies. Tint the
cells.

**A clickable row owes the full contract**: `cursor: pointer`, a hover state, a
`:focus-visible` ring, and keyboard reach. A row that only answers a mouse is
invisible to half its users.

**The header sticks.** `position: sticky; top: 0` on the header cells, an opaque
background, and a z-index from the scale. A header that scrolls away leaves every
column unlabelled from the second screen on.

**Numbers are tabular and right-aligned.** `font-variant-numeric: tabular-nums`, so
digits line up down the column and the column stops jittering as values change.
Text aligns left, numbers right, status and actions right or centred, and each
header aligns with its column.

**One separator.** A hairline rule between rows, or zebra striping — never both.

**Sorting announces itself.** `aria-sort` on the sorted header, a visible indicator,
and a real button. Reserve the indicator's space on every sortable header so the
headers do not shift sideways as the sort moves.

**Row actions do not hide behind hover.** Actions mounted on hover do not exist on
touch and are hard to find by keyboard. Render them at reduced emphasis and raise
them on hover and `focus-within`. Icon-only actions carry both `aria-label` and
`title`.

**Four states, not one blank body**: nothing yet, nothing matching the filter,
still loading, and the request failed. Loading is placeholder rows in the row's own
shape, inside the same table — see the SKL gates.

**Where rows animate in, the delay decays.** A flat 80ms stagger over two hundred
rows takes sixteen seconds. Decay it so early rows cascade and later ones arrive
almost together, with a floor of a few milliseconds, and stop it under reduced
motion.

```js
const BASE_ROW_DELAY_MS = 80, MIN_ROW_DELAY_MS = 6, DECAY_FACTOR = 0.88
```

**Status reads the same everywhere.** One mapping from status to tone, one badge
component, so a status in a table and the same status in a detail panel are the
same thing. Two spellings of one state is the fastest way to make a product feel
assembled from parts.

**It is a real table**: `table`, `thead`, `tbody`, `th` with `scope`, and a caption
or accessible name. On a phone it stacks into cards, scrolls inside its own
`overflow-x: auto` container, or drops to the columns that matter.

## Headers

**One rhythm on every surface.** Title, then supporting chips or counts, then a
flexible spacer, then actions — same order, same height, same padding, everywhere.
A header that reorders itself per section makes each section feel like a different
application. Extract one header component and pass the differences in.

**Sticky headers are opaque and layered.** A background token, a bottom rule or
shadow, and a z-index above in-page sticky elements. Only one thing sticks at
`top: 0`; secondary sticky elements offset by the header's height, which is why
that height is a token rather than three copies of `64px`.

**The header reserves its own space.** `scroll-margin-top` on anchor targets and
padding on the scroll container, or the first row lands underneath it.

**The title is the loudest thing in it** — one `h1`, heavier and larger than
anything beside it. Counts use tabular figures so the header does not reflow when a
number ticks. Below the breakpoint, labels collapse to icons or into an overflow
control, keeping their accessible names; the header never wraps into two rows.

```jsx
<div className="flex items-center gap-x-3 border-b px-4 py-2.5 bg-bg-primary">
  <h1 className="text-lg font-bold tracking-tight shrink-0">Messages</h1>
  <Badge tone="neutral"><span className="font-mono tabular-nums">{n}</span> conversations</Badge>
  <div className="flex-1 min-w-[8px]" />
  <button className="active:scale-[0.97] transition-transform duration-150 ease-out motion-reduce:transition-none">
    <i className="fas fa-check-double" />
    <span className="hidden sm:inline">Mark All Read</span>
  </button>
</div>
```

Note `align-items: center` on the row and `motion-reduce:transition-none` on the
press — both are gates, and both are one utility.

## Spacing relationships

The other spacing gates ask whether a value is on the scale. These ask whether the
space between two things is right, which is a different question and is only
answerable once the page is laid out. Run `scripts/code/spacing-probe.js` and feed
the result to `design-pass.py spacing-probe`.

**Two panels never share an edge.** Stacked bordered or surfaced blocks always have
space between them. At zero gap their borders meet and read as one double-ruled box
or as a rendering fault — and each panel is correct on its own, which is why it
survives review. This is the most common spacing defect in a generated form.

**The gap lives on the parent.** A column of blocks takes its spacing from the
container's `gap`, never from margins on its children. Margins collapse, differ
between first and last child, and go missing the moment a child renders
conditionally — leaving a hole exactly where the optional block used to be.

**Gaps within a stack are equal**, unless a deliberate break says otherwise. One
gap wider than its neighbours reads as a missing element.

**Grouping is carried by space.** The gap between groups is at least one scale step
larger than the gap inside a group, and the tiers are far enough apart to read as
hierarchy rather than as inconsistency — 16, 24, 32, 48, not 16, 18, 20.

**Nothing touches an edge.** Content keeps the container's padding on every side,
including beneath the last child, and that padding is on the container rather than
on the first and last child.

**Where blocks do sit flush by design** — a list, a segmented control, a table —
the shared edge draws one line, not two.
