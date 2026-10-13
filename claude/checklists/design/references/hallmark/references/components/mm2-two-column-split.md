### MM2 · Two-column split
Destinations on the left, one promoted thing on the right: the current release, a
featured case, a booking card. The right column is a different kind of content
rather than more links, which is what stops the panel reading as a longer menu.

*Use when:* the bar has one thing it actually wants clicked.
*Don't confuse with:* MM4, whose columns are peers.

```html
<div class="panel panel--split">
  <ul class="panel__rows">…</ul>
  <aside class="panel__feature"><img><b>Spring intake</b><a>Reserve</a></aside>
</div>
```

*Anti-pattern:* filling the right column with more links. Two peer columns is
MM4; pretending otherwise wastes the promoted slot.
