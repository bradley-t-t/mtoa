#!/usr/bin/env python3
"""The posting helper against stand-ins for Discord and ntfy, served from this process.

Slack's own path is the one the services have always used and is proven against the
real API by `python3 slack.py`; this covers the two that sit beside it: that settings()
routes each backend, that a post carries the embeds or the text, that a Discord message
can be read back and deleted, and that ntfy keeps what it posted for the read-back.

    python3 selftest_slack.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slack  # noqa: E402

SEEN = []
MESSAGES = {}
FAILURES = []


class Stand(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.append(("POST", self.path, dict(self.headers), body))
        if self.path.startswith("/discord"):
            ident = str(len(MESSAGES) + 1)
            MESSAGES[ident] = json.loads(body)
            self.reply(200, {"id": ident})
        else:
            self.reply(200, {"id": "n%d" % len(SEEN)})

    def do_GET(self):
        ident = self.path.rsplit("/", 1)[-1]
        if ident in MESSAGES:
            self.reply(200, MESSAGES[ident])
        else:
            self.reply(404, {"message": "Unknown Message"})

    def do_DELETE(self):
        ident = self.path.rsplit("/", 1)[-1]
        if MESSAGES.pop(ident, None) is None:
            self.reply(404, {"message": "Unknown Message"})
        else:
            self.reply(204)


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def main():
    server = HTTPServer(("127.0.0.1", 0), Stand)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port
    tmp = tempfile.mkdtemp()
    slack.SHARED_PATH = os.path.join(tmp, "slack.json")
    slack.NTFY_POSTED = os.path.join(tmp, "ntfy-posted.json")
    embed = {"title": "Uptime", "description": "**example.com** is down", "color": 0xE74C3C,
             "fields": [{"name": "Since", "value": "[the check](https://x.example)"}]}

    with open(slack.SHARED_PATH, "w") as handle:
        json.dump({"bot_token": "xoxb-1", "channel": "C1"}, handle)
    check("slack is the default", slack.settings(), ("xoxb-1", "C1"))
    check("a service's own channel wins", slack.settings({"slack_channel": "C2"}), ("xoxb-1", "C2"))

    with open(slack.SHARED_PATH, "w") as handle:
        json.dump({"backend": "discord", "discord_webhook": base + "/discord/hook"}, handle)
    token, channel = slack.settings()
    check("discord settings", (token.startswith("discord+"), channel), (True, "discord"))
    check("discord counts as configured", slack.configured(), True)
    ident = slack.post(token, channel, embed, username="Uptime")
    sent = MESSAGES[ident]
    check("discord gets the embed as built", sent["embeds"][0]["description"], "**example.com** is down")
    check("discord gets the name", sent.get("username"), "Uptime")
    check("discord read-back is slack-shaped", slack.posted_attachment(token, channel, ident)["text"],
          "*example.com* is down")
    slack.delete(token, channel, ident)
    check("discord delete", ident in MESSAGES, False)
    check("discord read-back of a deleted message", slack.posted_attachment(token, channel, ident), None)
    slack.delete(token, channel, ident)   # already gone is the outcome wanted

    with open(slack.SHARED_PATH, "w") as handle:
        json.dump({"backend": "ntfy", "ntfy_url": base + "/ntfy/topic", "ntfy_token": "tk_x"}, handle)
    token, channel = slack.settings()
    check("ntfy settings", channel, "ntfy")
    ident = slack.post(token, channel, embed)
    method, path, headers, body = SEEN[-1]
    check("ntfy posts to the topic", path, "/ntfy/topic")
    check("ntfy title", headers.get("Title"), "Uptime")
    check("ntfy auth", headers.get("Authorization"), "Bearer tk_x")
    check("ntfy body is markdown", body.decode(), "**example.com** is down\nSince: [the check](https://x.example)")
    check("ntfy read-back", slack.posted_attachment(token, channel, ident)["title"], "Uptime")
    slack.delete(token, channel, ident)
    check("ntfy delete forgets it", slack.posted_attachment(token, channel, ident), None)

    with open(slack.SHARED_PATH, "w") as handle:
        json.dump({"backend": "ntfy"}, handle)
    check("ntfy without a url is not configured", slack.configured(), False)

    server.shutdown()
    for line in FAILURES:
        print("FAIL " + line)
    print("all clear" if not FAILURES else "%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
