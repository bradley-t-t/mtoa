# The first paint

A page rendered to HTML at build time is on screen before the bundle is parsed. Whatever
its own head declares is what dresses it in that moment, and whatever the head leaves out
dresses nothing until the JavaScript that pulls it has run.

**Every built page links the sheets its own markup needs.** A bundler splits CSS the way
it splits code: a stylesheet imported by a module that is only reached through a lazy
route becomes an async chunk, linked at runtime by the loader rather than by the
document. The route's HTML is written separately, usually from the entry template's head,
and so ships with the entry sheet and nothing else. The markup it carries is complete;
the rules for it are not there yet.

What that looks like: the page paints its real elements against the browser's defaults.
A container that a missing rule was going to give a dark ground shows the body's ground
instead. An image sized by a missing rule renders at its intrinsic size, so a wordmark
meant to sit at 24px lands at 874px in the corner. Then the chunk arrives and the page
snaps into place. It reads as a loading screen nobody wrote.

The test needs no browser and no deployment. For each built page: the classes in its
markup, the sheets it links, and the sheets in the same build it does not. A class the
markup uses that an unlinked sheet defines and no linked sheet defines is markup that
paints bare. `scripts/probe.py paint` runs it over the build output, building first if
there is nothing to read.

The fix belongs in the build, not in a list: resolve each route's chunks from the bundle
graph and emit their stylesheet links into that route's head. Hoisting the import into
the entry sheet closes it too, and costs every other page the bytes.

How large the sheets are, and how long they hold the paint, is `/perf-checklist`. This
gate only asks whether the page that already rendered has the rules for what it rendered.
