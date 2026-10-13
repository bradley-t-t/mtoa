# Keeping your own setup

`~/.claude` is yours once it is installed. Edit the CLAUDE.md, add skills, tighten a
checklist. Two things keep that work safe.

**Updates never overwrite your edits.** `mtoa update` replaces an installed file only
when it is still the copy the last install wrote. A file you changed is left as it
is and named, so you can compare it with the new one and take what you want.

**Your setup can follow you between machines.** Make a private repository, set its
slug as `github.mirror` in the profile, and run:

```bash
~/.claude/tools/config-sync.py sync
```

That reconciles `~/.claude` with the mirror file by file, the later edit winning,
and pushes back. Run it from cron or a launch agent on every machine and an edit
made on one reaches the rest. A path is retired with `config-sync.py remove <path>`,
because a file deleted by hand comes back on the next sync.

A tool with cases in `tools/tests/` is judged by them before its clock, so an older
copy that fails its cases never overwrites a newer one that passes.
