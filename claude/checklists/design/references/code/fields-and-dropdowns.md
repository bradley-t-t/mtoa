# Fields and dropdowns

Two things a generated interface almost always gets wrong, because both look
finished before they are. A field inherits enough browser chrome to pass a glance,
and a dropdown is invisible until it opens, so nobody opens it.

Everything here is checked by the FLD gates.
`references/hallmark/references/interaction-and-states.md` carries the eight-state
contract these build on; this file is what to actually write.

## The field surface

A field has to read as a thing you type into, in dark, light, and gray.

```css
.field {
  /* Its own surface, distinguishable from the page ground it sits on. A field
     the same colour as the page is a label with a border. */
  background: var(--color-field);
  color: var(--color-ink);
  border: 1px solid var(--color-field-border);
  border-radius: var(--radius-md);

  /* One height token, shared with buttons and selects. A 38px input beside a
     44px button is the most common tuning miss in a generated form. */
  min-height: var(--control-height);
  padding-inline: var(--space-3);

  /* Under 16px, iOS zooms the page when the field takes focus. */
  font-size: max(16px, var(--text-sm));

  /* Reserved at rest so activating focus does not change the geometry. */
  outline: 2px solid transparent;
  outline-offset: 1px;
  transition: background-color 140ms var(--ease-out),
              border-color 140ms var(--ease-out);
}

.field:hover  { border-color: var(--color-field-border-hover); }
.field:focus-visible { outline-color: var(--color-focus); }
.field[aria-invalid="true"] { border-color: var(--color-danger); }
.field:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.field::placeholder { color: var(--color-ink-3); }
```

Rules that fall out of that block, and are each a gate:

- **The border needs 3:1 against the surface behind it.** It is a UI boundary, not
  decoration — a hairline that only just shows in light mode disappears in gray.
- **The placeholder is never the label.** It vanishes the moment there is content,
  taking the field's meaning with it. It also needs 4.5:1, which rules out the
  pale grey that comes for free.
- **Invalid is never colour alone.** `aria-invalid`, a message, and an icon or
  marker — a red border says nothing to a screen reader and little to a
  colourblind reader.
- **Border-width never changes between states.** State goes to colour, background,
  or outline. A 1px border becoming 2px on focus shifts every neighbour.
- **Autofill repaints the field** and ignores `color`. See the autofill block in
  `shadcn-theming.md`; without it, a returning visitor sees a form that no longer
  matches the page.

## Selects

A bare `<select>` renders OS chrome that ignores every token you set. Two honest
routes, and the choice gets stated:

**Style the native control.** Keeps keyboard, screen reader, and the mobile wheel
picker for free. The option list stays the platform's, which is a real limit and
usually the right trade.

```css
.select {
  appearance: none;              /* without this the OS arrow stays */
  background-image: url("data:image/svg+xml,…");   /* your chevron */
  background-repeat: no-repeat;
  background-position: right var(--space-3) center;
  padding-inline-end: var(--space-7);  /* room for the chevron */
}
```

**Build a listbox.** Only when the design needs styled options, multi-select
with custom rows, or search inside the list — and only if you replicate the whole
keyboard and screen-reader contract below. A `<div>` with a click handler is not a
select.

Either way: the trigger is a field, so everything in the block above applies to it.

## The menu surface

This is the part that gets skipped, because it is not on screen until someone opens
it.

```css
.menu {
  position: absolute;            /* never in flow - a menu must not push content */
  z-index: var(--z-dropdown);    /* from the layer scale, not 9999 */

  /* Opaque. A translucent menu over body text is unreadable, and it is the
     tell that nobody opened it over content. */
  background: var(--color-surface-raised);
  border: 1px solid var(--color-rule);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-menu);

  min-width: 100%;               /* at least the trigger's width */
  max-height: min(320px, 60vh);  /* never taller than the viewport */
  overflow-y: auto;
  overscroll-behavior: contain;  /* scrolling the menu does not scroll the page */
}
```

- **Nothing clips it.** A menu inside an `overflow: hidden` ancestor gets cut off at
  the container edge. Portal it to the body, or use the Popover API, which handles
  stacking and light-dismiss for free.
- **It flips at the viewport edge.** A menu near the bottom opens upward. Near the
  right edge it aligns right. Otherwise the last item is unreachable on a laptop.
- **It scales from the trigger,** not from its own centre — the popover
  transform-origin variable, 150–250ms, ease-out.
- **The width relates to the trigger** and does not jump between openings as the
  content changes.

## Options

Three states that have to be distinguishable from each other, not just from rest:

| State | What it means | Carried by |
| --- | --- | --- |
| Hover | the pointer is over it | surface tint |
| Focused | the keyboard is on it | surface tint plus a marker — usually a left rule or a ring |
| Selected | it is the current value | a checkmark or dot in a reserved gutter |

Hover and focus sharing one style is the common miss: arrow down through a menu
while the mouse rests on a different row and two rows look active. Selected must
not be carried by colour alone, and its marker sits in a gutter reserved on every
row so labels do not shift when the selection moves.

Long labels truncate with the full text reachable — `title`, a tooltip, or wrapping
at two lines. A menu also needs a no-results state and, if it fetches, a loading
one; an empty box reads as broken.

## The keyboard contract

If you built it, you owe all of it:

- Arrow keys cycle, wrapping at the ends. Home and End jump.
- Enter or Space selects and closes. Escape closes without selecting.
- Typing jumps to the matching option.
- `aria-expanded` on the trigger mirrors visibility; the menu is `role="listbox"` or
  `role="menu"`; options carry `aria-selected` or `aria-checked`.
- Focus returns to the trigger on close, and is trapped in the menu while open.
- Clicking outside closes it.

## Date, time, and colour pickers

Same rules, more surface. The trigger is a field, the panel is a menu, and the grid
inside it needs its own hover, focused, selected, today, out-of-range, and disabled
states — six, not three. Reach for the native input where the design allows it; the
platform pickers are better than what gets rebuilt in an afternoon.
