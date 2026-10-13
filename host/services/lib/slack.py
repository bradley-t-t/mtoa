#!/usr/bin/env python3
"""Everything the services post, posted to Slack, Discord or ntfy.

A Slack incoming webhook can only append, and the boards delete and repost what
they put up, so this talks to the Web API with a bot token. One token covers
every channel, so it lives once in ~/lib/slack.json rather than
being copied into each service's config:

    {"bot_token": "xoxb-...", "channel": "C0123456789"}

A service that needs its own channel sets `slack_channel` in its own config and
that wins. Nothing else in a service config has to change.

Slack's attachments are the nearest thing to a Discord embed - a coloured bar,
a title, a body, name/value fields - so `attachment` translates one into the
other and the services keep building the embeds they always built.

Slack is the default. A Discord webhook or an ntfy topic takes its place with
`backend` in the same file:

    {"backend": "discord", "discord_webhook": "https://discord.com/api/webhooks/..."}
    {"backend": "ntfy", "ntfy_url": "https://ntfy.sh/my-topic", "ntfy_token": "tk_..."}

The services never see the difference: settings() hands back a token and a
channel as before, and post, delete and posted_attachment route on the token.
Discord takes the embeds as they are built. ntfy gets them as plain text, and
since it cannot delete a message, a board on ntfy posts a new one only when what
it shows changes.

Deployed to ~/lib/ and reached as ../lib from a service directory.
Run it directly to prove the token, the channel and the scopes:

    python3 ~/lib/slack.py
"""

import json
import os
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://slack.com/api/"
USER_AGENT = "sunday-boards/1.0"
SHARED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack.json")
NTFY_POSTED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntfy-posted.json")

# Deleting a message that is already gone is the outcome we wanted, so these
# read as success rather than as something to raise on.
GONE = {"message_not_found", "channel_not_found"}


class SlackError(RuntimeError):
    """Slack answers 200 with ok:false, so its errors have to be raised by hand."""


def call(token, method, payload, timeout=15):
    request = Request(API + method, data=json.dumps(payload).encode(), method="POST", headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer %s" % token,
    })
    with urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    if not body.get("ok"):
        raise SlackError(body.get("error", "unknown"))
    return body


def get(token, method, params, timeout=15):
    """The read methods want their arguments in the query string, not a body."""
    request = Request(API + method + "?" + urlencode(params), headers={
        "User-Agent": USER_AGENT,
        "Authorization": "Bearer %s" % token,
    })
    with urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    if not body.get("ok"):
        raise SlackError(body.get("error", "unknown"))
    return body


def settings(config=None):
    """Token and channel: the shared file, with a service's own channel winning.

    For Discord and ntfy the token is the webhook or topic URL marked with its
    backend, and the channel names the backend, so a caller checking that both
    are set reads them exactly as it reads Slack's."""
    try:
        with open(SHARED_PATH) as handle:
            shared = json.load(handle)
    except (OSError, ValueError):
        shared = {}
    own = config or {}
    backend = own.get("notify_backend") or shared.get("backend") or "slack"
    if backend == "discord":
        url = own.get("discord_webhook") or shared.get("discord_webhook") or ""
        return ("discord+" + url if url else ""), "discord"
    if backend == "ntfy":
        url = own.get("ntfy_url") or shared.get("ntfy_url") or ""
        auth = own.get("ntfy_token") or shared.get("ntfy_token") or ""
        return ("ntfy+" + url + ("#" + auth if auth else "") if url else ""), "ntfy"
    token = own.get("slack_bot_token") or shared.get("bot_token") or ""
    channel = own.get("slack_channel") or shared.get("channel") or ""
    return token, channel


def _route(token):
    """Which backend a token from settings() belongs to, and the token itself."""
    for name in ("discord", "ntfy"):
        if token.startswith(name + "+"):
            return name, token[len(name) + 1:]
    return "slack", token


def _http(method, url, body=None, headers=None, timeout=15):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    request = Request(url, data=data, method=method, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else {}


def _discord_embed(embed):
    out = dict(embed)
    if isinstance(out.get("color"), str):
        out["color"] = int(out["color"].lstrip("#"), 16)
    return out


def _utc(match):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(match.group(1))))


def _as_text(embeds):
    """Embeds as markdown lines, for a backend that shows text alone. The embeds are
    written in Discord's markdown, which is the common kind bar its timestamps."""
    lines = []
    stamp = lambda text: re.sub(r"<t:(\d+)(?::[a-zA-Z])?>", _utc, text)
    for embed in embeds:
        if embed.get("description"):
            lines.append(stamp(embed["description"]))
        for field in embed.get("fields") or []:
            lines.append("%s: %s" % (field.get("name", ""), stamp(field.get("value", ""))))
        if embed.get("url"):
            lines.append(embed["url"])
    return "\n".join(lines) or "Update"


def _ntfy_posted(update=None, drop=None):
    try:
        with open(NTFY_POSTED) as handle:
            posted = json.load(handle)
    except (OSError, ValueError):
        posted = {}
    if update or drop:
        posted.update(update or {})
        posted.pop(drop, None)
        posted = dict(list(posted.items())[-50:])
        with open(NTFY_POSTED + ".tmp", "w") as handle:
            json.dump(posted, handle)
        os.replace(NTFY_POSTED + ".tmp", NTFY_POSTED)
    return posted


def configured(config=None):
    token, channel = settings(config)
    return bool(token and channel)


def _date(match):
    """Discord's <t:epoch:style> as Slack's date token, in the reader's own zone."""
    epoch = int(match.group(1))
    fallback = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))
    return "<!date^%d^{date_short_pretty} at {time}|%s>" % (epoch, fallback)


def mrkdwn(text):
    """Discord markdown as Slack writes it: links inverted, bold single-starred."""
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    text = re.sub(r"<t:(\d+)(?::[a-zA-Z])?>", _date, text)
    return re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)


def plain(text):
    """What Slack renders, back in the characters we sent it.

    Reading a message back is not the same as reading what was posted: Slack
    turns a bare domain into a link and a unicode symbol into a :name:, so a
    board comparing itself against its source has to undo both first.
    """
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    return text


def color_hex(value):
    """Discord takes a colour as an integer, Slack as #rrggbb."""
    if isinstance(value, str):
        return value if value.startswith("#") else "#" + value
    return "#%06x" % (value or 0)


def attachment(embed):
    """A Discord embed as Slack's nearest equivalent."""
    out = {"color": color_hex(embed.get("color")), "mrkdwn_in": ["text", "fields"]}
    if embed.get("title"):
        out["title"] = mrkdwn(embed["title"])
    if embed.get("url"):
        out["title_link"] = embed["url"]
    if embed.get("description"):
        out["text"] = mrkdwn(embed["description"])
    if embed.get("fields"):
        out["fields"] = [
            {"title": field.get("name", ""), "value": mrkdwn(field.get("value", "")), "short": False}
            for field in embed["fields"]
        ]
    return out


def post(token, channel, embeds, username=None, fallback=None):
    """Posts one message carrying these embeds. Returns its timestamp, the id."""
    if isinstance(embeds, dict):
        embeds = [embeds]
    backend, token = _route(token)
    if backend == "discord":
        payload = {"embeds": [_discord_embed(e) for e in embeds][:10]}
        if username:
            payload["username"] = username
        if fallback and not embeds:
            payload["content"] = fallback
        sep = "&" if "?" in token else "?"
        return _http("POST", token + sep + "wait=true", payload,
                     {"Content-Type": "application/json"})["id"]
    if backend == "ntfy":
        url, _, auth = token.partition("#")
        title = fallback or (embeds[0].get("title") if embeds else "") or "Update"
        headers = {"Title": plain(mrkdwn(title)).encode("ascii", "replace").decode(), "Markdown": "yes"}
        if auth:
            headers["Authorization"] = "Bearer " + auth
        sent = _http("POST", url, _as_text(embeds).encode(), headers)
        ident = str(sent.get("id") or time.time())
        _ntfy_posted({ident: attachment(embeds[0]) if embeds else {}})
        return ident
    payload = {
        "channel": channel,
        # The fallback is what a notification and a screen reader get, so it
        # cannot be empty when everything else lives in an attachment.
        "text": fallback or (embeds[0].get("title") if embeds else "") or "Update",
        "attachments": [attachment(embed) for embed in embeds],
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if username:
        payload["username"] = username
    return call(token, "chat.postMessage", payload)["ts"]


def delete(token, channel, ts):
    backend, token = _route(token)
    if backend == "discord":
        try:
            _http("DELETE", token.split("?")[0] + "/messages/" + str(ts))
        except OSError as error:
            if getattr(error, "code", None) != 404:
                raise
        return
    if backend == "ntfy":
        _ntfy_posted(drop=str(ts))
        return
    try:
        call(token, "chat.delete", {"channel": channel, "ts": ts})
    except SlackError as error:
        if str(error) not in GONE:
            raise


def posted_attachment(token, channel, ts):
    """The first attachment on the message at ts, or None if it is gone."""
    backend, token = _route(token)
    if backend == "discord":
        try:
            message = _http("GET", token.split("?")[0] + "/messages/" + str(ts))
        except OSError as error:
            if getattr(error, "code", None) == 404:
                return None
            raise
        embeds = message.get("embeds") or []
        return attachment(embeds[0]) if embeds else None
    if backend == "ntfy":
        return _ntfy_posted().get(str(ts))
    try:
        body = get(token, "conversations.history",
                   {"channel": channel, "latest": ts, "inclusive": "true", "limit": 1})
    except SlackError as error:
        if str(error) in GONE:
            return None
        raise
    messages = body.get("messages") or []
    if not messages or messages[0].get("ts") != ts:
        return None
    attachments = messages[0].get("attachments") or []
    return attachments[0] if attachments else None


def main():
    token, channel = settings()
    if not (token and channel):
        print("slack: %s is missing bot_token and channel (or a discord_webhook or ntfy_url)" % SHARED_PATH)
        return 1
    ts = post(token, channel, {"title": "Sunday is wired up", "description": "Deleting this line now.",
                               "color": 0x95A5A6})
    delete(token, channel, ts)
    print("slack: posted and deleted in %s — token, channel and scopes are good" % channel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
