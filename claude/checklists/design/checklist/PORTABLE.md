# Running this skill where ~/.claude does not exist

The gates and the tool that runs them ship together, in `bin/design-pass.py`, and
that tool resolves its registry from its own location rather than from a fixed path
under `~/.claude`. So the skill works from wherever it was installed — a synced
config directory, an uploaded account skill, a cloud container that has the skill
and nothing else.

What travels with the skill:

- the 519 gates in `checklist/gates/`
- `bin/design-pass.py`, which runs them
- every reference the gates cite, under `references/`
- the probes in `scripts/code/`

What does not, and cannot:

- the hooks. `hook-edit`, `guard-stop`, and `guard-land` are declared in
  `~/.claude/settings.json`, which is machine configuration rather than part of a
  skill. Without them nothing opens a run on its own and nothing refuses to end a
  session with one open — the checklist still runs, but only because the skill says
  to.

That gap is worth stating out loud in an environment that has the skill and not the
hooks, rather than letting the run look identical to one that was enforced.

```bash
bin/design-pass.py gates            # confirm the registry loaded
bin/design-pass.py start --kind web-ui --target .
bin/design-pass.py verify
```
