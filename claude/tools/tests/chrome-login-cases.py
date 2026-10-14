#!/usr/bin/env python3
"""Cases the Chrome sign-in host has to get right.

The host's value is in what it refuses: a password handed to a page on the
wrong origin, handed over without approval, or written to a caller that is not
Chrome has leaked, and nothing afterwards recovers it. So the cases pin those
paths shut first and prove the happy path second.

  chrome-login-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "chrome-login.py")

# Stand-ins for what the vault holds. The cases assert where these may and may
# not appear, and none of that turns on their shape.
SECRET = "fixture-password-not-a-credential"
OTHER = "fixture-password-second-account"
FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeVault:
    """The vault as the host reaches it: MCP tool calls over one session."""

    def __init__(self, entries):
        # entries: name -> (tags, sealed text)
        self.entries = dict(entries)
        self.reads = []
        self.puts = []

    def _call(self, method, params):
        name, args = params["name"], params["arguments"]
        if name == "credential_list":
            listing = [{"name": n, "namespace": "logins", "tags": tags}
                       for n, (tags, _) in sorted(self.entries.items())]
            return {"content": [{"type": "text", "text": json.dumps(listing)}]}
        if name == "credential_get":
            self.reads.append(args["name"])
            entry = self.entries.get(args["name"])
            return {"content": [{"type": "text",
                                 "text": entry[1] if entry else "not found: " + args["name"]}]}
        if name == "credential_put":
            self.puts.append(args)
            return {"content": [{"type": "text", "text": "stored: " + args["name"]}]}
        return {"isError": True, "content": [{"type": "text", "text": "no such tool"}]}

    def close(self):
        pass


def sealed(origins, username, password, **extra):
    return json.dumps(dict(origins=origins, username=username, password=password, **extra))


def main():
    mod = load()

    # --- origins
    check("origin is normalized", mod.origin_of("HTTPS://GitHub.com:443/login?next=/"),
          "https://github.com")
    check("a port that is not the default stays", mod.origin_of("http://localhost:8080/a"),
          "http://localhost:8080")
    check("an app address has no origin", mod.origin_of("android://abc@com.example/"), None)
    check("a script address has no origin", mod.origin_of("javascript:alert(1)"), None)
    check("a wildcard admits a subdomain",
          mod.origin_matches("https://*.example.com", "https://login.example.com"), True)
    check("a wildcard does not admit the apex",
          mod.origin_matches("https://*.example.com", "https://example.com"), False)
    check("a wildcard does not admit a lookalike",
          mod.origin_matches("https://*.example.com", "https://evilexample.com"), False)
    check("a wildcard does not cross schemes",
          mod.origin_matches("https://*.example.com", "http://login.example.com"), False)
    check("an exact origin does not admit another port",
          mod.origin_matches("https://example.com", "https://example.com:8443"), False)
    check("lookup tags climb to the registrable name",
          mod.lookup_tags("https://a.b.example.com"),
          ["host:a.b.example.com", "host:*.b.example.com", "host:*.example.com"])
    check("a bad wildcard is refused", mod.pattern_of("https://*.com/path"), None)

    # --- records
    check("a seed is cleaned", mod.normalize_totp("jbsw y3dp ehpk 3pxp"),
          ("JBSWY3DPEHPK3PXP", 6, 30))
    check("an otpauth link carries its digits and period",
          mod.normalize_totp("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP&digits=8&period=60"),
          ("JBSWY3DPEHPK3PXP", 8, 60))
    for bad in ("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP&algorithm=SHA256", "not base32 !!", ""):
        try:
            mod.normalize_totp(bad)
            FAILURES.append(f"seed {bad!r} was accepted")
        except ValueError:
            pass
    for origins, user, password in ((["https://x.test"], "me", ""), (["ftp://x.test"], "me", "p"),
                                    ([], "me", "p"), (["https://x.test"], " ", "p")):
        try:
            mod.make_record(origins, user, password)
            FAILURES.append(f"record {origins!r} {user!r} was accepted")
        except ValueError:
            pass
    record = mod.make_record(["https://GitHub.com/login", "https://*.github.com"], " me@x.test ", SECRET)
    check("a record keeps patterns in order", record["origins"],
          ["https://github.com", "https://*.github.com"])
    check("a record is named by host and user", mod.entry_name(record), "github.com:me@x.test")
    check("a record is tagged for lookup", mod.entry_tags(record),
          ["login", "host:github.com", "host:*.github.com", "user:me@x.test"])

    rows = [
        {"name": "a", "url": "https://a.test/login", "username": "me", "password": "one"},
        {"name": "a", "url": "https://a.test/other", "username": "me", "password": "two"},
        {"name": "app", "url": "android://hash@com.example/", "username": "me", "password": "x"},
        {"name": "b", "url": "https://b.test/", "username": "me", "password": ""},
    ]
    records, skipped = mod.logins_from_csv(rows)
    check("an export collapses repeats to the later row", [(r["origins"], r["password"]) for r in records],
          [(["https://a.test"], "two")])
    check("an export names what it skipped", skipped,
          {"not a web address": 1, "no username or password": 1})

    # --- fill: the sealed origin decides, whatever the tags say
    vault = FakeVault({
        # Tagged for github.com by a session with write access, sealed for another site.
        "github.com:rewritten": (["login", "host:github.com", "user:rewritten"],
                                 sealed(["https://evil.test"], "rewritten", OTHER)),
    })
    asked = []
    reply = mod.handle({"action": "fill", "origin": "https://github.com"},
                       vault_factory=lambda: vault, approver=lambda r: asked.append(r) or True)
    check("a rewritten tag opens nothing", reply.get("ok"), False)
    check("a rewritten tag is never put to approval", asked, [])
    check("a rewritten tag hands over nothing", OTHER in json.dumps(reply), False)

    vault = FakeVault({
        "github.com:me": (["login", "host:github.com", "user:me"],
                          sealed(["https://github.com"], "me", SECRET)),
        "gitlab.com:me": (["login", "host:gitlab.com", "user:me"],
                          sealed(["https://gitlab.com"], "me", OTHER)),
    })
    asked = []
    reply = mod.handle({"action": "fill", "origin": "https://github.com/login"},
                       vault_factory=lambda: vault, approver=lambda r: asked.append(r) or True)
    check("an approved fill hands over the login", (reply.get("ok"), reply.get("password")),
          (True, SECRET))
    check("approval names the site and the account", asked, ["sign in to github.com as me"])
    check("a fill opens only the entries tagged for the site", vault.reads, ["github.com:me"])
    check("a login without a seed carries no code", "totp" in reply, False)

    reply = mod.handle({"action": "fill", "origin": "https://github.com"},
                       vault_factory=lambda: vault, approver=lambda r: False)
    check("a refused approval hands over nothing", (reply.get("ok"), SECRET in json.dumps(reply)),
          (False, False))

    reply = mod.handle({"action": "fill", "origin": "https://evil.test"},
                       vault_factory=lambda: vault, approver=lambda r: True)
    check("another site gets nothing", (reply.get("ok"), SECRET in json.dumps(reply)), (False, False))

    vault = FakeVault({
        "x.test:a": (["login", "host:x.test", "user:a"], sealed(["https://x.test"], "a", SECRET)),
        "x.test:b": (["login", "host:x.test", "user:b"],
                     sealed(["https://x.test"], "b", OTHER, totp="JBSWY3DPEHPK3PXP",
                            totp_digits=6, totp_period=30)),
    })
    asked = []
    reply = mod.handle({"action": "fill", "origin": "https://x.test"},
                       vault_factory=lambda: vault, approver=lambda r: asked.append(r) or True)
    check("two accounts ask which", (reply.get("ok"), reply.get("accounts"), asked),
          (False, ["a", "b"], []))
    reply = mod.handle({"action": "fill", "origin": "https://x.test", "account": "b"},
                       vault_factory=lambda: vault, approver=lambda r: True)
    check("a named account is the one handed over", (reply.get("username"), reply.get("password")),
          ("b", OTHER))
    check("a seed travels with its shape", reply.get("totp"),
          {"seed": "JBSWY3DPEHPK3PXP", "digits": 6, "period": 30})

    vault = FakeVault({
        "example.com:me": (["login", "host:*.example.com", "user:me"],
                           sealed(["https://*.example.com"], "me", SECRET)),
    })
    reply = mod.handle({"action": "fill", "origin": "https://accounts.example.com"},
                       vault_factory=lambda: vault, approver=lambda r: True)
    check("a wildcard login fills a subdomain", reply.get("password"), SECRET)

    # --- list and save
    vault = FakeVault({
        "github.com:me": (["login", "host:github.com", "user:me"],
                          sealed(["https://github.com"], "me", SECRET)),
    })
    reply = mod.handle({"action": "list", "origin": "https://github.com"}, vault_factory=lambda: vault)
    check("a listing names the account", reply.get("logins"),
          [{"entry": "github.com:me", "usernames": ["me"]}])
    check("a listing opens no secret", vault.reads, [])

    vault = FakeVault({})
    reply = mod.handle({"action": "save", "origin": "https://site.test/login", "username": "me",
                        "password": SECRET, "totp": "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP",
                        "extraOrigins": ["https://*.site.test"]}, vault_factory=lambda: vault)
    check("a save answers with the entry", (reply.get("ok"), reply.get("entry")), (True, "site.test:me"))
    put = vault.puts[0] if vault.puts else {}
    check("a save lands in the logins namespace", put.get("namespace"), "logins")
    check("a save seals the origins with the password", json.loads(put.get("secret", "{}")).get("origins"),
          ["https://site.test", "https://*.site.test"])
    check("a save keeps the password out of the metadata",
          SECRET in json.dumps({k: v for k, v in put.items() if k != "secret"}), False)
    check("a save answer carries no secret", SECRET in json.dumps(reply), False)
    reply = mod.handle({"action": "save", "origin": "chrome://settings", "username": "me",
                        "password": SECRET}, vault_factory=lambda: FakeVault({}))
    check("a save off the web is refused", reply.get("ok"), False)

    # --- the host refuses any caller but Chrome
    request = json.dumps({"action": "fill", "origin": "https://github.com"}).encode()
    done = subprocess.run([sys.executable, str(TARGET), "host", mod.CALLER],
                          input=struct.pack("=I", len(request)) + request,
                          capture_output=True, timeout=30)
    check("a host started from a shell refuses", done.returncode, 2)
    check("a host started from a shell writes nothing", done.stdout, b"")
    done = subprocess.run([sys.executable, str(TARGET), "host", "chrome-extension://other/"],
                          input=b"", capture_output=True, timeout=30)
    check("a host started for another extension refuses", (done.returncode, done.stdout), (2, b""))

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("chrome-login: every case passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
