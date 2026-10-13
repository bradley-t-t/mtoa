### MM6 · Inline expand row
No floating card. The bar's own height grows and the destinations lay out inside
it, so the chrome stays one object rather than a bar with a sheet under it.

*Use when:* a minimal bar where a dropped card would read as heavier than the
site.
*Don't confuse with:* MM4, which is a separate full-width surface below the bar.

```html
<header class="nav is-expanded">
  <div class="nav__inner">…</div>
  <div class="nav__expand">…</div>
</header>
```

*Anti-pattern:* animating height on a panel whose content height is unknown. Use
a grid-rows transition so it settles at its own size.
