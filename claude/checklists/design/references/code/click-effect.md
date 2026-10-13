# Click Effect

A click effect is what the page does at the cursor when it is clicked, wherever it
is clicked. It is not the press feedback on the control that was hit — that is a
`:active` scale on the button and it answers for the button. A site can have every
pressable answering the finger and still feel inert, because nothing answers the
click.

Gates PTR-31 and PTR-32 hold web UI to this. Add one where a site has none.

## What it is not

Three rules elsewhere sit close to this and are about other things:

- The custom-cursor ban replaces the pointer with a drawn one, which costs the
  reader the affordance the operating system gave them. A click effect leaves the
  cursor alone.
- The particle-system ban is about ambient decoration that runs whether or not
  anyone is there. A click effect exists only in response to a click and is gone in
  under half a second.
- The slop test fails decoration with no semantic anchor. A click has an anchor:
  the reader did something, and the interface acknowledged it.

## The implementation

One listener, one layer, one short animation. The layer is fixed, covers the
viewport, and never takes a click.

```css
.click-layer {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
}

.click-ripple {
  position: absolute;
  width: 12px;
  height: 12px;
  margin: -6px 0 0 -6px;
  border-radius: 50%;
  border: 1.5px solid var(--accent);
  opacity: 0.9;
  animation: click-ripple 480ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes click-ripple {
  to {
    transform: scale(4.5);
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .click-ripple { display: none; }
}
```

```js
const layer = document.createElement('div')
layer.className = 'click-layer'
document.body.appendChild(layer)

const still = window.matchMedia('(prefers-reduced-motion: reduce)')

document.addEventListener('pointerdown', event => {
  if (still.matches || event.button !== 0) return
  const ripple = document.createElement('span')
  ripple.className = 'click-ripple'
  ripple.style.left = `${event.clientX}px`
  ripple.style.top = `${event.clientY}px`
  layer.appendChild(ripple)
  ripple.addEventListener('animationend', () => ripple.remove())
})
```

## Choosing the effect

The ripple above is the conservative default and belongs on most sites. Where the
brand has somewhere else to go, it goes there: a spark burst suits a product with
energy in its voice, a soft radial glow suits a quiet one, a single expanding
hairline suits an editorial page. The rules that hold whichever is chosen:

- It reads within 100ms of the press and is finished inside 600ms.
- It takes its colour from the accent already in the tokens, never a new hue.
- It draws on the fixed layer, never inside the element that was clicked, so it is
  not clipped by an `overflow: hidden` ancestor.
- Left button only. A right-click is opening a menu and does not want a flourish
  underneath it.
- It is one effect for the whole site, not a different one per page.
