### MM1 · Icon-row list
A single column of rows, each an icon tile, a title and one line of description.
The default a bar reaches for, and the one worth spending deliberately: it reads
instantly and it says nothing about the product.

*Use when:* four to six destinations of equal weight, each needing a sentence.
*Don't confuse with:* MM3, which groups rows under headings; MM1 has no groups.

```html
<div class="panel">
  <p class="panel__summary">Book the track for a group.</p>
  <ul class="panel__rows">
    <li><a><span class="panel__icon">…</span><span><b>Birthday parties</b><em>Room for 60.</em></span></a></li>
  </ul>
</div>
```

*Anti-pattern:* a description on some rows and not others. The ragged right edge
reads as unfinished rather than varied.
