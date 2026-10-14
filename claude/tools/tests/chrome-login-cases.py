#!/usr/bin/env python3
"""Cases the Chrome sign-in filler has to get right.

What matters here is mostly what must not happen. A password leaves the vault
only after the owner approved that sign-in, and only for a tab on a host the
record itself names; it is never printed; a step the site refuses is never
submitted twice; and the relay between the command line and the extension
carries a request and its answer and nothing else. The happy path is pinned
too: a three-step sign-in - username, password, one-time code - is filled one
step at a time with only the value each step asks for.

  chrome-login-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "chrome-login.py")
HOST = TARGET.parent / "chrome-login" / "host.py"

# Stand-ins for what the vault holds. The cases assert where these go and where
# they never show up, and none of that turns on their shape.
PASSWORD = "fixture-password-not-a-credential"
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def payload(hosts=("example.com",), totp=True):
    data = {"v": 1, "hosts": list(hosts), "username": "owner@example.com",
            "password": PASSWORD}
    if totp:
        data["totp"] = {"secret": SEED, "digits": 6, "period": 30, "algorithm": "SHA1"}
    return data


class FakeStore:
    def __init__(self, records):
        self.records = records
        self.reads = []

    def logins(self):
        return [{"name": name, "description": "owner@example.com",
                 "tags": ["chrome-login"] + ["host:%s" % h for h in rec["hosts"]]}
                for name, rec in sorted(self.records.items())]

    def secret(self, name):
        self.reads.append(name)
        return self.records[name]

    def close(self):
        pass


class FakeChrome:
    """A tab whose sign-in moves through `steps`, one per submitted fill."""

    def __init__(self, steps, url="https://example.com/login", lands_on=None):
        self.steps = list(steps)
        self.url = url
        self.lands_on = lands_on
        self.stuck = False
        self.fills = []

    def relays(self):
        return ["relay"]

    def tabs(self):
        return [{"tabId": 7, "url": self.url, "active": True, "front": True, "relay": "relay"},
                {"tabId": 8, "url": "https://elsewhere.test/", "active": False, "front": False,
                 "relay": "relay"}]

    def _answer(self, hosts, extra=None):
        host = self.lands_on if (self.lands_on and not self.steps) else "example.com"
        if host not in hosts:
            return {"host": host, "refused": True, "frames": []}
        fields = dict.fromkeys(("username", "password", "totp"), False)
        if self.steps:
            fields.update(dict.fromkeys(self.steps[0], True))
        return {"host": host, "refused": False, "frames": [dict(fields, **(extra or {}))]}

    def probe(self, tab, hosts):
        return self._answer(hosts)

    def fill(self, tab, hosts, values, submit):
        self.fills.append(dict(values))
        shown = self.steps[0] if self.steps else ()
        answer = self._answer(hosts, {"filled": [k for k in values if k in shown]})
        if submit and self.steps and set(shown) <= set(values) and not self.stuck:
            self.steps.pop(0)
        return answer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def totp_cases(mod):
    # RFC 6238 appendix B, SHA1 with the 20-byte ASCII key "1234567890" twice.
    eight = {"secret": SEED, "digits": 8, "period": 30, "algorithm": "SHA1"}
    for at, want in ((59, "94287082"), (1111111109, "07081804"),
                     (1234567890, "89005924"), (2000000000, "69279037")):
        check("RFC 6238 SHA1 at %d" % at, mod.totp_code(eight, at), want)
    seed256 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA"
    check("RFC 6238 SHA256 at 59",
          mod.totp_code({"secret": seed256, "digits": 8, "period": 30,
                         "algorithm": "SHA256"}, 59), "46119246")
    parsed = mod.parse_totp("otpauth://totp/Example:owner?secret=%s&issuer=Example&digits=8"
                            % SEED.lower())
    check("an otpauth URI is read", (parsed["secret"], parsed["digits"]), (SEED, 8))
    check("a spaced seed is accepted", mod.parse_totp("gezd gnbv gy3t qojq")["secret"],
          "GEZDGNBVGY3TQOJQ")
    for bad in ("not base32 at all!", "otpauth://hotp/x?secret=%s" % SEED):
        try:
            mod.parse_totp(bad)
            check("a bad seed is refused: %s" % bad, False, True)
        except ValueError:
            pass


def host_cases(mod):
    check("a URL is reduced to its host", mod.normalize_host("https://GitHub.com/login"),
          "github.com")
    check("a port is dropped", mod.normalize_host("localhost:5173"), "localhost")
    try:
        mod.normalize_host("not a host")
        check("a non-host is refused", False, True)
    except ValueError:
        pass
    try:
        mod.parse_payload(json.dumps({"hosts": [], "username": "u", "password": "p"}))
        check("a record naming no host is refused", False, True)
    except mod.Refused:
        pass


def choice_cases(mod):
    tabs = [{"tabId": 1, "url": "https://example.com/a", "active": False, "front": False},
            {"tabId": 2, "url": "https://example.com/b", "active": True, "front": True},
            {"tabId": 3, "url": "https://example.com/c", "active": True, "front": False}]
    check("the tab in front wins", mod.choose_tab(tabs, ["example.com"])["tabId"], 2)
    check("--tab names one", mod.choose_tab(tabs, ["example.com"], tab_id=1)["tabId"], 1)
    check("--url names one", mod.choose_tab(tabs, ["example.com"], url="https://example.com/c")
          ["tabId"], 3)
    try:
        mod.choose_tab([dict(t, front=False, active=False) for t in tabs], ["example.com"])
        check("several unranked tabs are refused", False, True)
    except mod.Refused:
        pass
    store = FakeStore({"one": payload(), "two": payload()})
    try:
        mod.choose(store.logins(), tabs)
        check("two logins on one host need a name", False, True)
    except mod.Refused as problem:
        check("the refusal names both logins", "one, two" in str(problem), True)
    meta, tab = mod.choose(store.logins(), tabs, name="two")
    check("a named login is used", (meta["name"], tab["tabId"]), ("two", 2))


def run_fill(mod, chrome, store, verdict="approved", **kwargs):
    asked, said, clock = [], [], Clock()

    def approver(reason):
        asked.append(reason)
        return verdict
    out = io.StringIO()
    status = 0
    with redirect_stdout(out):
        try:
            mod.fill(kwargs.pop("name", None), store=store, chrome=chrome, approver=approver,
                     say=said.append, clock=clock, sleep=clock.sleep, **kwargs)
        except mod.Refused as problem:
            said.append("refused: %s" % problem)
            status = problem.status
    return status, asked, said + [out.getvalue()]


def fill_cases(mod):
    # Approval refused: the vault is never read and nothing is filled.
    chrome = FakeChrome([("username", "password")])
    store = FakeStore({"site": payload()})
    status, asked, said = run_fill(mod, chrome, store, verdict="denied")
    check("a denied sign-in exits 3", status, 3)
    check("a denied sign-in reads nothing", store.reads, [])
    check("a denied sign-in fills nothing", chrome.fills, [])
    check("the prompt names the host and account", asked,
          ["sign in to example.com as owner@example.com in Chrome"])

    # No sign-in fields: the owner is never asked.
    chrome = FakeChrome([])
    store = FakeStore({"site": payload()})
    status, asked, _ = run_fill(mod, chrome, store)
    check("a page with no fields is refused", status, 1)
    check("a page with no fields asks nobody", asked, [])

    # The record's own hosts outrank its metadata.
    chrome = FakeChrome([("username", "password")])
    store = FakeStore({"site": payload(hosts=("example.com",))})
    store.secret = lambda name: payload(hosts=("other.test",))
    status, _, said = run_fill(mod, chrome, store)
    check("a record not for this host is refused", status, 1)
    check("a record not for this host fills nothing", chrome.fills, [])
    check("a record not for this host is refused before the tab is touched",
          "is not for example.com" in said[-2], True)

    # Three steps, each handed only what it asks for.
    chrome = FakeChrome([("username",), ("password",), ("totp",)])
    store = FakeStore({"site": payload()})
    status, asked, said = run_fill(mod, chrome, store)
    check("a three-step sign-in finishes", status, 0)
    check("the username step gets the username alone", chrome.fills[0],
          {"username": "owner@example.com"})
    check("the password step gets the password alone", chrome.fills[1], {"password": PASSWORD})
    check("the code step gets a six-digit code alone",
          (list(chrome.fills[2]), len(chrome.fills[2]["totp"])), (["totp"], 6))
    check("the owner is asked once", len(asked), 1)
    check("nothing printed carries the password", any(PASSWORD in s for s in said), False)

    # A refused password is never submitted twice.
    chrome = FakeChrome([("username", "password")])
    chrome.stuck = True
    store = FakeStore({"site": payload()})
    status, _, said = run_fill(mod, chrome, store)
    check("a password the site refuses stops the sign-in", status, 1)
    check("a password the site refuses is filled once", len(chrome.fills), 1)
    check("the refusal says the step is still showing", "still shows" in said[-2], True)

    # A site asking for a code the login lacks is named, not guessed at.
    chrome = FakeChrome([("username", "password"), ("totp",)])
    store = FakeStore({"site": payload(totp=False)})
    status, _, said = run_fill(mod, chrome, store)
    check("a missing seed is refused", (status, "one-time code" in said[-2]), (1, True))

    # Leaving the login's hosts after the password is the sign-in finishing.
    chrome = FakeChrome([("username", "password")], lands_on="app.example.org")
    store = FakeStore({"site": payload(totp=False)})
    status, _, said = run_fill(mod, chrome, store)
    check("a redirect after the password finishes", status, 0)

    # --no-submit fills once and stops.
    chrome = FakeChrome([("username", "password")])
    store = FakeStore({"site": payload()})
    status, _, _ = run_fill(mod, chrome, store, submit=False)
    check("--no-submit fills once", (status, len(chrome.fills)), (0, 1))


def add_cases(mod):
    try:
        mod.add("site", interactive=False)
        check("add refuses a non-terminal", False, True)
    except mod.Refused as problem:
        check("add refuses a non-terminal with usage status", problem.status, 2)

    stored = {}

    class Sink:
        def put(self, name, data):
            stored[name] = data

        def close(self):
            pass
    answers = iter([PASSWORD, PASSWORD, ""])
    hosts = mod.add("site", hosts=["https://Example.com/login", "www.example.com"],
                    username="owner@example.com", secret=lambda prompt: next(answers),
                    store=Sink(), interactive=True)
    check("add stores normalized hosts", hosts, ["example.com", "www.example.com"])
    check("add stores the record", stored["site"]["password"], PASSWORD)
    answers = iter([PASSWORD, "different", ""])
    try:
        mod.add("site", hosts=["example.com"], username="u", secret=lambda prompt: next(answers),
                store=Sink(), interactive=True)
        check("mismatched passwords are refused", False, True)
    except mod.Refused:
        pass


def relay_cases(mod):
    with tempfile.TemporaryDirectory(dir="/tmp") as root:
        env = dict(os.environ, INTELLIGENCE_STATE_DIR=root)
        host = subprocess.Popen([sys.executable, str(HOST)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env=env)
        try:
            sockets = Path(root) / "chrome-login" / "hosts"
            deadline = time.time() + 5
            while time.time() < deadline and not list(sockets.glob("*.sock")):
                time.sleep(0.05)
            relays = list(sockets.glob("*.sock"))
            check("the host opens one socket", len(relays), 1)
            if not relays:
                return
            check("the socket is the owner's alone", relays[0].stat().st_mode & 0o077, 0)
            chrome = mod.Chrome(sockets)
            result = {}

            def call():
                result["tabs"] = chrome.ask(relays[0], {"op": "tabs"}, timeout=5)
            import threading
            caller = threading.Thread(target=call)
            caller.start()
            size = struct.unpack("=I", host.stdout.read(4))[0]
            request = json.loads(host.stdout.read(size))
            check("the request reaches the extension", request.get("op"), "tabs")
            body = json.dumps({"id": request["id"], "ok": True,
                               "result": [{"tabId": 1, "url": "https://example.com/"}]}).encode()
            host.stdin.write(struct.pack("=I", len(body)) + body)
            host.stdin.flush()
            caller.join(5)
            check("the answer reaches the caller", result.get("tabs"),
                  [{"tabId": 1, "url": "https://example.com/"}])
        finally:
            host.stdin.close()
            host.wait(5)
        check("the host leaves no socket behind", list(sockets.glob("*.sock")), [])


def main():
    mod = load()
    totp_cases(mod)
    host_cases(mod)
    choice_cases(mod)
    fill_cases(mod)
    add_cases(mod)
    relay_cases(mod)
    for row in FAILURES:
        print("FAIL " + row)
    print("%d case(s) failed." % len(FAILURES) if FAILURES else "All cases pass.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
