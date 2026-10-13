### MM5 · Preview on hover
A list on one side; hovering a row swaps the panel's other side to that row's
image, stat or blurb. The panel teaches while it navigates, and the swap is the
only motion in it.

*Use when:* destinations are visually distinct and worth previewing.
*Don't confuse with:* MM2, whose right column is fixed.

```html
<div class="panel panel--preview">
  <ul class="panel__rows" data-preview>…</ul>
  <figure class="panel__preview">…</figure>
</div>
```

*Anti-pattern:* leaving the preview blank until first hover. Seed it with the
first row so the panel opens complete.
