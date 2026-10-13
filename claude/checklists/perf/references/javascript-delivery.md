# JavaScript delivery

On a single-page app the bundle is the page. The rules here change which bytes arrive
first and which arrive when something needs them. None of them changes what the code
does once it runs.

## Route splitting

The commonest defect, and the largest win. A router that imports every page statically
produces one chunk containing the whole site:

```jsx
import HomePage from "./pages/HomePage";
import CheckoutPage from "./pages/CheckoutPage";
import StaffPanelPage from "./pages/StaffPanelPage";
```

A visitor reading the home page downloads the checkout and the staff panel before
anything is drawn. Behind a dynamic import, each route arrives when it is entered:

```jsx
const HomePage = lazy(() => import("./pages/HomePage"));
const CheckoutPage = lazy(() => import("./pages/CheckoutPage"));

<Suspense fallback={<RouteSkeleton />}>
  <Routes>...</Routes>
</Suspense>
```

The fallback matters. A `<Suspense>` fallback that is shorter than the route it stands
in for moves everything below it when the route lands, which is layout shift introduced
by a performance fix. It reserves the real height, and the parity capture catches it
when it does not.

The first route is the exception: eager-loading whichever route the entry lands on
avoids a needless round trip on the one page that matters most.

## Duplicated capability

Two libraries doing the same job are both downloaded and one is used. The pattern is
easy to miss because each arrived for a good reason months apart. Real example from
this workspace: `aos`, `framer-motion` and `motion` all in one manifest, with
`framer-motion` imported by nothing at all.

```bash
scripts/bundle-probe.py deps --root .
```

names them, along with what imports each, and with anything declared and never imported.

## Heavy optional work

A library only some visitors need belongs behind the interaction that needs it:

```js
async function openCheckout() {
  const { loadStripe } = await import("@stripe/stripe-js");
  const stripe = await loadStripe(key);
}
```

Payment SDKs, WebGL, editors, chart engines, PDF and video tooling, and map libraries
are the usual candidates. Each is measured in hundreds of kilobytes and each is used by
a minority of sessions.

## Server-only packages in a browser bundle

`openai`, `stripe` (the server SDK, not `@stripe/stripe-js`), database clients, mailers
and anything expecting a secret in the environment do not belong in client code. Beyond
the weight, the code either ships a credential or fails at runtime, and both are worse
problems than the size. The call moves behind an endpoint; the browser talks to your own
route.

## Chunking for the cache

Application code changes on every deploy; its dependencies change rarely. One chunk
throws away a returning visitor's entire cache each time:

```js
build: {
  rollupOptions: {
    output: {
      manualChunks: {
        react: ["react", "react-dom", "react-router-dom"],
        motion: ["motion"],
      },
    },
  },
}
```

Split by change frequency rather than by size. Too many small chunks costs round trips
and defeats compression, which works better over more text.

## Discovery

Each level of the import graph the browser has not been told about is a serial round
trip: fetch the entry, parse it, discover the next chunk, fetch that. `modulepreload`
links in the document declare the graph up front so the fetches overlap. Most bundlers
emit them; the thing to confirm is that they are in the built HTML.

## Barrels

```js
// src/components/index.js
export * from "./Button";
export * from "./Modal";
export * from "./Chart";
```

One named import from that file pulls the whole directory wherever anything in it has a
side effect, and side effects are hard to rule out. Importing from the module directly
avoids the question.

## Build target

A default target from years ago transpiles working syntax into more code that does the
same thing more slowly, and ships polyfills for syntax every current browser runs. An
explicit target says what the site's visitors actually run:

```js
build: { target: "es2020" }
```

Anything supporting `<script type="module">` needs almost none of what a default
transpile emits.

## Budgets that hold

```bash
scripts/bundle-probe.py budget --root . --write
```

records the current weights as a ceiling, keeping the lower of the existing and the new
one. In CI, the next change that adds a megabyte fails the build.
