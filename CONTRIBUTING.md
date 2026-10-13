# Contributing

Issues and pull requests are welcome. This repository is published from the setup I
work in every day, so a pull request merged here is carried back into that setup by
hand and comes out again in the next release. Expect a short wait between the merge
and the change reaching `main` in its final form.

Before you open one:

- Keep names out of it. Who you are, your hosts and your accounts go in a profile
  key, never in the code. If the change needs a new key, add it to
  `docs/profile.md`.
- Run the checks CI runs: the cases in `claude/tools/tests/`, the guard selftests,
  and `python3 bin/mtoa doctor` against a clean home.

```bash
for t in claude/tools/tests/*-cases.py; do HOME=$(mktemp -d) python3 "$t" || echo "FAILED $t"; done
python3 claude/ratchet/ratchet.py selftest
```
