# Vendored: the avoid-ai-writing skill

`SKILL.md` here is the upstream skill (MIT, Conor Bronsdon), at the version its
own frontmatter declares. It is upstream's file. Do not hand-edit it: a local
change is lost the next time the skill is replaced, and it makes the diff
against upstream unreadable. That is also why the comment, documentation, and
writing ledgers skip any directory carrying this marker.

The detection engine that ships with this skill is vendored separately at
`tools/ai-writing/`, where `writing-pass.py` runs it without the skill being
loaded; that copy's own `VENDOR.md` says how the two update together.

To update: replace `SKILL.md` with the new upstream release and run the writing
pass over a repo that scored clean before, since a category rename upstream
shows up as a gate that blocks on nothing.
