# The host

One always-on Linux machine runs everything that is not the sites themselves. Mine
is a Raspberry Pi 5 on my desk; a small VPS or a NAS works as well. Its failures
are silent, so the first thing to learn is the heartbeat: the status feed is rebuilt
every few minutes, and a feed older than fifteen minutes means the host is down,
whatever the ticket queue says.

```bash
~/.claude/tools/mtoa_config.py get host.url   # then fetch <url>/status.json
```

## What runs there

| Service | What it does | How it runs |
| :--- | :--- | :--- |
| `error-reporter` | Takes browser error reports from your sites at `/report`, files them as issues in `github.tickets`, and serves `status.json` | always on |
| `uptime-monitor` | Checks every site each minute and posts when one goes down or comes back | timer, 60 s |
| `status-board`, `queue-board`, `attention-board` | Keep one message each up to date: the sites, the open tickets, what needs you | timers, 2 min |
| `expiry-monitor` | Warns before a certificate or a domain runs out | daily |
| `error-monitor` | Has Claude Code work the ticket queue on its own, fix what it can and open pull requests | cron, every 10 min |
| `routine-repair` | Reads a routine that failed, fixes the routine, and runs it again | cron, every 5 min |

The boards and alerts post through `~/lib/slack.json`. Slack is the default;
Discord and ntfy work too:

```json
{"bot_token": "xoxb-...", "channel": "C0123456789"}
{"backend": "discord", "discord_webhook": "https://discord.com/api/webhooks/..."}
{"backend": "ntfy", "ntfy_url": "https://ntfy.sh/your-topic"}
```

`python3 ~/lib/slack.py` posts a test line and deletes it.

## Setting one up with systemd

On the host:

1. Install Python 3, git, [`gh`](https://cli.github.com) and
   [Claude Code](https://claude.com/claude-code), then `gh auth login` and sign
   Claude Code in. The routines run under that login.
2. Install the setup there too, so the routines have the tools and your profile:
   `curl -fsSL https://raw.githubusercontent.com/bradley-t-t/mtoa/main/install.sh | bash`,
   then copy your `~/.claude/profile/` across.

On your own machine, with `host.ssh` set in the profile:

```bash
~/.mtoa/bin/mtoa host check        # what differs between this checkout and the host
~/.mtoa/bin/mtoa host push         # copy the services across and restart what changed
~/.mtoa/bin/mtoa host units push   # install the systemd units, rendered for the host's account
```

Then, on the host, give each service its `config.json` (start from the
`config.example.json` beside it), enable the timers, and add the two cron lines:

```bash
sudo systemctl enable --now error-reporter uptime-monitor.timer status-board.timer \
  queue-board.timer attention-board.timer expiry-monitor.timer
crontab -e
#   */10 * * * *  ~/bin/error-monitor-tick.sh
#   */5 * * * *   ~/bin/routine-repair-sweep.sh
```

The error reporter listens on loopback. Put it behind whatever already fronts the
machine: `tailscale serve`, Caddy, or nginx.

## With Docker instead

The services that need no Claude Code login run in one container. The routines that
work tickets need the real host above.

```bash
mkdir -p mtoa-home/error-reporter
cp host/services/error-reporter/config.example.json mtoa-home/error-reporter/config.json
# edit it, and add "listen_host": "0.0.0.0"
docker compose -f host/docker/compose.yml up -d --build
```

Each service starts once its `mtoa-home/<service>/config.json` exists, and the log
names the ones that are not set up yet.

## Adding a site

The error reporter's snippet is `host/snippet/reporter.html`. Add the site's origin to
`allowed_origins` in the reporter's config and its name to `repo_map`; the
onboarding steps are in `host/docs/onboarding.md`.
