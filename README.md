# M.T.O.A.

My Take on AI: the Claude Code setup I work in every day, the small always-on host
that watches my sites and works their tickets, and Sunday, the voice assistant that
talks to both. It is opinionated, and the opinions are the point. Take all of it or
the parts you want.

```bash
curl -fsSL https://raw.githubusercontent.com/bradley-t-t/mtoa/main/install.sh | bash
```

That clones this repository to `~/.mtoa`, installs the Claude setup into `~/.claude`
and starts a profile for you. Nothing already in `~/.claude` is overwritten without
`--force`, and anything `--force` replaces is kept under `~/.claude/.mtoa-replaced/`.
Then:

```bash
~/.mtoa/bin/mtoa doctor
```

## What is in it

**`claude/`** is what lands in `~/.claude`.

- `CLAUDE.md` is the take itself: the reply shape (a verdict, then Why, What,
  Touched and Proof), staying on the question that was asked, finishing work
  before saying it is done, and a git workflow of `develop` for work and `main`
  for what is live.
- `ratchet/` holds the hooks that enforce it. A reply that names work the turn
  could have done is sent back, and so is a reply that leaves the shape.
- `checklists/` holds six checklists a public site answers to: design, SEO,
  performance, stack, database, and Google's own accounts. Each one is an
  instruction to change the project, never an audit. `/checklist` runs all six.
- `skills/` holds `/commit`, `/pretrip`, `/merge` and `/ship` for the release
  flow, `/predator` for hunting AI slop in a codebase, `/validate`, `/notes`,
  `/draft`, `/inspire`, `/3d` and the rest.
- `tools/` holds what those call: version bumps, release lag, the writing pass,
  the SVG guard, the setup sync, and `with-credential.py`, which runs a command with
  a secret in its environment and nowhere else.

**`host/`** is a set of small services for one always-on Linux machine (mine is a
Raspberry Pi 5). It runs an error reporter your sites post browser errors to, which
files them as GitHub issues; an uptime monitor; status, queue and attention boards
posted to Slack, Discord or ntfy; certificate and domain expiry warnings; and a
routine that has Claude Code work the ticket queue on its own every night.

**`voice/`** is Sunday. The server runs on the host: speech recognition and the
voice are local models, and Claude Code is the brain. Every answer goes through a
speech gate before a word is said, so it does not repeat itself, does not claim
work is done before it is, and does not tell you to go type something. You talk to
it through a speaker cube, a Mac app, or any computer with a microphone.

## The profile

Nothing in the code names anyone. Who you are, which GitHub owners are yours, where
your host is and what the assistant is called all live in one file,
`~/.claude/profile/mtoa.json`, beside `~/.claude/profile/CLAUDE.md` for the rules
that are yours alone. A key you leave empty turns its feature off rather than
guessing.

```bash
~/.mtoa/bin/mtoa config show
~/.mtoa/bin/mtoa config get github.owners
```

[`docs/profile.md`](docs/profile.md) lists every key.

## Requirements

- macOS or Linux, Python 3.9 or newer, git, and [Claude Code](https://claude.com/claude-code).
- [`gh`](https://cli.github.com), signed in, for the release skills and the host's tickets.
- For the host: a Linux machine with systemd, Python 3, and Claude Code signed in
  there for the routines. [`docs/host.md`](docs/host.md) also covers Docker for the
  services that do not need Claude.
- For the voice: the host above with about 4 GB of free memory. [`docs/voice.md`](docs/voice.md).

## Credentials

Secrets never go in the profile. `secrets.backend` names where they are kept: a
`chmod 600` env file (the default), the macOS Keychain, 1Password, or
[CryptoFort](https://github.com/bradley-t-t/cryptofort). See
[`docs/credentials.md`](docs/credentials.md).

## Keeping it current

```bash
~/.mtoa/bin/mtoa update
```

pulls this repository and installs again. A file you have changed since the last
install is left alone and named, so your edits are never overwritten by an update.
To carry your own setup between machines, point `github.mirror` in the profile at a
private repository of yours and run `~/.claude/tools/config-sync.py sync`; see
[`docs/keeping-your-own.md`](docs/keeping-your-own.md).

## Marketing skills

Several skills reach for the marketing skills by Corey Haines when a project has
pages that sell. They are not copied here. Install them from their source:

```bash
npx skills add coreyhaines31/marketingskills
```

## License

MIT. The pieces that come from elsewhere keep their own licenses; see
[`THIRD_PARTY.md`](THIRD_PARTY.md).
