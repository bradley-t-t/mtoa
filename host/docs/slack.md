# Where the Pi posts

Everything the Pi says goes to one Slack channel - errors as they arrive, the
three boards, uptime, certificate and registration expiry. The Monday social
scorecard is the one thing that goes to Discord instead, because this channel is
the attention board's and the scorecard is nothing anybody has to act on.

Slack takes an app rather than a webhook. A Slack incoming webhook can only
append, and the boards delete and repost what they put up, so posting goes
through the Web API with a bot token. One token covers every channel, so it is
held once rather than copied into six configs.

The app is called Sunday, after the machine the messages come off.

## The shared config

`/home/mtoa/lib/slack.json`, readable only by its owner:

```json
{
  "bot_token": "xoxb-...",
  "channel": "C0123456789"
}
```

Every service reads it through `lib/slack.py`. A service that needs a different
channel sets `slack_channel` in its own config and that wins; nothing else in a
service config has anything to do with Slack.

## Rebuilding the app

`services/slack/manifest.yaml` is the app's whole configuration - name, bot user
and scopes - so it can be rebuilt without remembering what was ticked.

At https://api.slack.com/apps: Create New App, From a manifest, pick the
workspace, paste that file, then Create and Install. The install hands back the
`xoxb-` token, which does not expire.

Put it in place off the clipboard, so it is never typed or left in shell
history:

```bash
pbpaste | ssh voice-host "python3 /home/mtoa/lib/set_slack.py C0123456789"
```

The channel id is the `C...` string, not the name. It is at the bottom of the
channel's View Channel Details.

The bot puts itself in a channel - `channels:join` is in the manifest - so
nothing has to be invited by hand. An archived channel refuses that, and
refuses to be unarchived by a bot that is not in it, so unarchive it yourself
first from Channel Details, Settings.

## Proving it

Posts a line and deletes it again, which exercises the token, the channel and
both scopes every board needs, and leaves nothing behind:

```bash
ssh voice-host "python3 /home/mtoa/lib/slack.py"
```

The status board can go further and compare what it is showing against the feed
it was built from:

```bash
ssh voice-host "python3 /home/mtoa/status-board/status_board.py --check"
```

## What lib/slack.py does

It holds the API calls and two translations, because the services were written
against Discord and still build the same messages.

Discord takes an embed - a coloured bar, a title, a body, name/value fields -
and Slack's nearest equivalent is an attachment, so `attachment()` converts one
into the other and no service had to have its message rewritten.

The text inside needs converting too: Discord writes `[text](url)`, `**bold**`
and `<t:epoch:f>` where Slack writes `<url|text>`, `*bold*` and a date token.
`plain()` runs that backwards, which `--check` needs - reading a message back
is not the same as reading what was sent, because Slack turns a bare domain
into a link and a symbol into a `:name:`.
