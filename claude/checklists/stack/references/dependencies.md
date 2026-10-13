# Dependencies

Every entry in `package.json` is weight, attack surface, and a thing that can be
abandoned upstream while the project keeps installing it.

**Unused.** A dependency nobody imports is installed on every machine and in every
build, and stays in the audit surface long after the code that needed it went. Things
used only by a config file or a script are real uses — name them in the note rather
than removing them.

**Duplicated.** Two copies of React or Next in the resolved tree break hooks and
context in ways that read as application bugs, and the error names neither package. It
happens through a transitive dependency pinning its own copy; an override is usually
the fix.

**Pinned outside the registry.** A `git+`, `file:` or `link:` dependency builds on the
machine that has it and fails in CI. A git pin can also move under the project without
the lockfile changing meaningfully. Publish it or vendor it.

**Advisories.** High and critical are not judgement calls. Lower severities are, and an
advisory whose code path this project never reaches is answered with that reason rather
than with the number.

**Deprecated upstream.** A deprecation is the maintainer saying they have stopped,
which means the next advisory against that package will not be fixed. The registry's
`deprecated` field on the latest version is the signal.
