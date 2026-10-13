# Vendored: the avoid-ai-writing detector

`patterns.js` and `validate.js` are the detection engine and preservation
validator that ship with the `avoid-ai-writing` skill (MIT, Conor Bronsdon),
copied here so `writing-pass.py` can score text without the skill being loaded.
They are upstream's files. Do not hand-edit them: a local change is lost the
next time the engine is replaced, and it makes the diff against upstream
unreadable. That is also why the comment, documentation, and writing ledgers
skip any directory carrying this marker.

`scan.js` is this repo's own. It is a thin seam over `patterns.js` — argument
handling, the self-reference exemption, and paragraph chunking for documents
past the engine's word ceiling — and it exists so the engine underneath it can
be swapped wholesale.

To update: take the new `detector/patterns.js` and `detector/validate.js` from
the skill at the version `skills/avoid-ai-writing/SKILL.md` declares, drop them
in, and run the writing pass over a repo that scored clean before. A category
rename upstream shows up as a file that suddenly blocks on nothing, because
`writing-pass.py` gates on category names it holds itself.

`LICENSE` is upstream's MIT terms and travels with the code.
