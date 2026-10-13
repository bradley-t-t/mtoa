# Tooling

Every gate here catches the same failure in a different place: a control that exists to
be pointed at rather than to catch anything.

**A formatter with only a write script cannot gate.** Running it makes the check pass.
`format:check` is the one CI calls.

**A linter that extends one preset and adds no rules agrees with everything.** The gate
is that the config turns rules on, not that a config file exists.

**`--if-present` passes silently.** The standard workflow runs `npm run lint
--if-present`, so a project with no lint script has a green CI and no linting. That is
why the script and the workflow step are checked as one thing.

**Strict off is a type checker that agrees with the code.** And most bundlers transpile
without checking types at all, so `tsc --noEmit` has to run somewhere — if not in CI,
nowhere.

**A bare `eslint-disable` turns off every rule on that line** for whoever reads it
next, and records nothing about what it was for. Naming the rule costs one word.

**Two lockfiles are two dependency trees**, and whichever tool ran last wins. None
means CI resolves versions nobody has run. One, committed.

**`engines.node` and the workflow's `node-version` have to be the same major**, or the
build in CI differs from the build anybody ran, exactly in the places the two majors
differ.

Editor configuration is personal. `.idea/`, `.vscode/`, `*.iml` and `.fleet/` are
ignored, and none of them is tracked.
