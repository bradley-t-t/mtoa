#!/usr/bin/env python3
"""Sign in to one of the owner's accounts in Chrome without a secret reaching a session.

A session driving Chrome reaches sign-in pages it cannot pass: typing a password
puts it in the transcript, and the transcript outlives the page. This is the way
through. The session names the sign-in, the owner approves it with Touch ID, and
the username, password and one-time code travel from the CryptoFort vault into
the page inside this process and the Chrome Login extension, rendered nowhere a
person or a log can read them.

  chrome-login.py fill [LOGIN] [--tab ID | --url PREFIX] [--username U] [--no-submit]
  chrome-login.py list                 every enrolled login, without its secret
  chrome-login.py add LOGIN            enrol or replace one, from your own terminal
  chrome-login.py install              build the approval prompt, register the relay
  chrome-login.py status               what is built, registered, loaded and connected

`fill` finds the tab on a host the login names (the one in front when several
are, or the one --tab names, which is the tab id the Chrome tools report),
checks that it shows sign-in fields, and only then asks for approval. The
prompt names the host and the account. Approved, it fills and submits each step
the site shows in turn - username, password, then the one-time code - and stops
when the sign-in fields are gone, or when a step is still showing fifteen
seconds after it was submitted, which is a refusal or a challenge for the
session to look at. A step is submitted once and never retried, so a wrong
password cannot walk an account into a lockout.

A login is one vault record in the `chrome-login` namespace. Its secret is a JSON
object holding the hosts it may be filled on, the username, the password and an
optional TOTP seed; its metadata carries the hosts again for finding it, and the
username, neither of which is secret. The hosts inside the secret are the ones
enforced, so rewriting a record's metadata cannot send its password anywhere
new, and a page is filled only over https, or http on this machine's loopback.

A host the vault holds no login for is looked up among the passwords Chrome has
saved, in every profile. The match is the exact host of the saved sign-on realm,
https only, the most recently used first unless --username names one. After the
Touch ID approval, macOS asks the owner to release Chrome's own storage key from
the login keychain; the password is opened with it, copied into the vault as a
`chrome-login` record named for the host, and filled. From then on the vault is
the source and only Touch ID is asked. Safari's passwords live in a keychain
macOS opens to Apple's own apps alone, so they are not a source.

Exit status: 0 filled, 1 refused or failed, 2 usage, 3 not approved, 4 not set up.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import importlib.util
import json
import ctypes
import os
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = Path(__file__).resolve().parent
KIT = HERE / "chrome-login"
NAMESPACE = "chrome-login"
TAG = "chrome-login"
RELAY = "com.mtoa.chrome_login"
# Fixed by the public key in extension/manifest.json, so the relay registration
# can name the one extension allowed to start it before it is ever loaded.
EXTENSION_ID = "kgknokedjcdclhdhcjkmfjaajkjcblnh"
CHROME = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*"
                      r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$|^\[::1\]$")
LOGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
STEP_LIMIT = 15
FLOW_LIMIT = 120


class Refused(Exception):
    """A fill that stopped before or during the sign-in, with the reason."""

    def __init__(self, message, status=1):
        super().__init__(message)
        self.status = status


def _load(name, filename):
    for path in (HERE / filename, Path.home() / ".claude" / "tools" / filename):
        if path.is_file():
            spec = importlib.util.spec_from_file_location(name, str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise Refused("%s is missing beside this tool" % filename, 4)


def state_dir():
    return _load("_lib", "_lib.py").state_root() / "chrome-login"


def approver_path():
    return state_dir() / "chrome-login"


# ---------------------------------------------------------------- one-time codes

def parse_totp(text):
    """A TOTP seed as base32 or an otpauth:// URI, checked by computing a code."""
    text = text.strip()
    config = {"secret": text, "digits": 6, "period": 30, "algorithm": "SHA1"}
    if text.lower().startswith("otpauth://"):
        url = urlparse(text)
        if url.netloc.lower() != "totp":
            raise ValueError("only time-based (totp) codes are supported")
        query = {k.lower(): v[0] for k, v in parse_qs(url.query).items()}
        config["secret"] = unquote(query.get("secret", ""))
        config["digits"] = int(query.get("digits", 6))
        config["period"] = int(query.get("period", 30))
        config["algorithm"] = query.get("algorithm", "SHA1").upper()
    config["secret"] = re.sub(r"[\s-]", "", config["secret"]).upper().rstrip("=")
    if config["algorithm"] not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError("unsupported algorithm %s" % config["algorithm"])
    if not 6 <= config["digits"] <= 8 or config["period"] <= 0:
        raise ValueError("digits must be 6 to 8 and the period positive")
    totp_code(config)
    return config


def totp_code(config, at=None):
    """RFC 6238: the code for the period holding `at` (now by default)."""
    secret = config["secret"]
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    except (ValueError, TypeError):
        raise ValueError("the one-time code seed is not base32")
    if not key:
        raise ValueError("the one-time code seed is empty")
    counter = int((time.time() if at is None else at) // config["period"])
    digest = hmac.new(key, struct.pack(">Q", counter),
                      getattr(hashlib, config["algorithm"].lower())).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** config["digits"]).zfill(config["digits"])


# ---------------------------------------------------------------- logins

def normalize_host(text):
    text = text.strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/")[0]
    if text != "[::1]":
        text = text.split(":")[0]
    if not HOSTNAME.match(text):
        raise ValueError("%r is not a host name" % text)
    return text


def host_of(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    return "[%s]" % host if ":" in host else host


def parse_payload(text):
    """The decrypted record: hosts, username, password, and an optional TOTP seed."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise Refused("the vault record is not a chrome-login record")
    hosts = data.get("hosts")
    if (not isinstance(hosts, list) or not hosts
            or not all(isinstance(h, str) and HOSTNAME.match(h) for h in hosts)):
        raise Refused("the vault record names no valid hosts")
    if not isinstance(data.get("username"), str) or not isinstance(data.get("password"), str):
        raise Refused("the vault record holds no username and password")
    if data.get("totp") is not None and not isinstance(data["totp"], dict):
        raise Refused("the vault record's one-time code seed is malformed")
    return data


def login_hosts(meta):
    return [t[len("host:"):] for t in meta.get("tags", []) if t.startswith("host:")]


class Store:
    """The CryptoFort vault, over the same private session with-credential.py opens."""

    def __init__(self):
        self.vault = _load("with_credential", "with-credential.py").Vault()

    def _text(self, tool, arguments):
        result = self.vault._call("tools/call", {"name": tool, "arguments": arguments})
        texts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
        if result.get("isError"):
            raise Refused("the vault refused %s: %s" % (tool, " ".join(texts).strip()[:200]))
        return "".join(texts)

    def logins(self):
        listed = json.loads(self._text("credential_list", {"namespace": NAMESPACE, "tags": [TAG]})
                            or "[]")
        return sorted(listed, key=lambda m: m.get("name", ""))

    def secret(self, name):
        text = self._text("credential_get", {"name": name, "namespace": NAMESPACE})
        if text.startswith("not found:"):
            raise Refused("the vault holds no login named %s" % name)
        return parse_payload(text)

    def put(self, name, payload):
        self._text("credential_put", {
            "name": name, "namespace": NAMESPACE, "secret": json.dumps(payload),
            "provider": payload["hosts"][0], "description": payload["username"],
            "tags": [TAG] + ["host:%s" % h for h in payload["hosts"]]})

    def close(self):
        self.vault.close()


# ---------------------------------------------------------------- Chrome's saved passwords

def aes_cbc(decrypt, key, iv, data):
    """AES-CBC with PKCS7 padding through CommonCrypto, which every Mac carries."""
    lib = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    lib.CCCrypt.restype = ctypes.c_int32
    lib.CCCrypt.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p,
                            ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
                            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    out = ctypes.create_string_buffer(len(data) + 16)
    moved = ctypes.c_size_t(0)
    status = lib.CCCrypt(1 if decrypt else 0, 0, 1, key, len(key), iv, data, len(data), out,
                         len(out), ctypes.byref(moved))
    if status != 0:
        raise Refused("Chrome's sealed password would not open (CommonCrypto %d)" % status)
    return out.raw[:moved.value]


def chrome_saved(root=None):
    """Every https login Chrome has saved, in every profile, without its password."""
    root = Path(root) if root else CHROME
    rows = []
    for db in sorted(root.glob("*/Login Data")) + sorted(root.glob("*/Login Data For Account")):
        # Read in place and immutable: Chrome holds the file open, and a copy of
        # a password store left on disk is one more place for it to leak from.
        try:
            conn = sqlite3.connect("file:%s?mode=ro&immutable=1" % quote(str(db)), uri=True)
        except sqlite3.Error:
            continue
        try:
            found = conn.execute(
                "select signon_realm, username_value, password_value, date_last_used, "
                "date_password_modified from logins where blacklisted_by_user = 0 "
                "and scheme = 0").fetchall()
        except sqlite3.Error:
            found = []
        finally:
            conn.close()
        for realm, username, sealed, used, modified in found:
            host = host_of(realm or "")
            if not (realm or "").startswith("https://") or not host or not username or not sealed:
                continue
            rows.append({"host": host, "username": username, "sealed": bytes(sealed),
                         "profile": db.parent.name, "used": max(used or 0, modified or 0)})
    return sorted(rows, key=lambda r: -r["used"])


def chrome_key(run=subprocess.run):
    """Chrome's storage key, which macOS releases only once the owner allows it."""
    try:
        done = run(["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage",
                    "-a", "Chrome"], capture_output=True, text=True, timeout=150)
    except subprocess.TimeoutExpired:
        done = None
    if done is None or done.returncode != 0 or not done.stdout.strip():
        raise Refused("macOS did not release Chrome's password key from the keychain", 3)
    return hashlib.pbkdf2_hmac("sha1", done.stdout.strip().encode("utf-8"), b"saltysalt", 1003, 16)


def chrome_open(sealed, key):
    if sealed[:3] not in (b"v10", b"v11"):
        raise Refused("Chrome sealed this password in a form this tool cannot open")
    try:
        return aes_cbc(True, key, b" " * 16, sealed[3:]).decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("Chrome's password key did not open this password")


def take_from_chrome(row, logins, store, key=chrome_key):
    """Copy one saved Chrome login into the vault, returning its name and record."""
    payload = {"v": 1, "hosts": [row["host"]], "username": row["username"],
               "password": chrome_open(row["sealed"], key())}
    taken = {m.get("name") for m in logins}
    name = row["host"]
    count = 2
    while name in taken:
        name = "%s-%d" % (row["host"], count)
        count += 1
    store.put(name, payload)
    return name, payload


# ---------------------------------------------------------------- the browser

class Chrome:
    """Every Chrome profile with the extension loaded, each through its own relay."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else state_dir() / "hosts"

    def relays(self):
        return sorted(self.directory.glob("*.sock")) if self.directory.is_dir() else []

    def ask(self, relay, request, timeout=30):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout + 5)
        try:
            conn.connect(str(relay))
            conn.sendall((json.dumps(dict(request, timeout=timeout)) + "\n").encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            conn.close()
        answer = json.loads(data.decode("utf-8") or "{}")
        if not answer.get("ok"):
            raise RuntimeError(answer.get("error") or "the extension gave no answer")
        return answer.get("result")

    def tabs(self):
        found = []
        for relay in self.relays():
            try:
                rows = self.ask(relay, {"op": "tabs"}, timeout=10)
            except (OSError, RuntimeError, ValueError):
                continue
            for row in rows or []:
                found.append(dict(row, relay=str(relay)))
        return found

    def probe(self, tab, hosts, front=False):
        return self.ask(tab["relay"], {"op": "probe", "tabId": tab["tabId"], "hosts": hosts,
                                       "front": front})

    def fill(self, tab, hosts, values, submit):
        return self.ask(tab["relay"], {"op": "fill", "tabId": tab["tabId"], "hosts": hosts,
                                       "values": values, "submit": submit})


def fields_of(answer):
    """Which sign-in fields any allowed frame of the tab shows."""
    frames = answer.get("frames") or []
    return {k: any(f.get(k) for f in frames) for k in ("username", "password", "totp")}


def choose_tab(tabs, hosts, tab_id=None, url=None):
    matching = [t for t in tabs if host_of(t.get("url", "")) in hosts]
    if tab_id is not None:
        matching = [t for t in matching if t.get("tabId") == tab_id]
    if url:
        matching = [t for t in matching if t.get("url", "").startswith(url)]
    if not matching:
        raise Refused("no open tab is on %s" % (
            ", ".join(hosts) if len(hosts) <= 4 else "a host with a login the vault or Chrome holds"))
    for key in ("front", "active"):
        picked = [t for t in matching if t.get(key)]
        if len(picked) == 1:
            return picked[0]
    if len(matching) == 1:
        return matching[0]
    raise Refused("several tabs are on %s; name one with --tab: %s" % (
        ", ".join(hosts), "; ".join("%s %s" % (t["tabId"], t["url"]) for t in matching)))


def choose(logins, tabs, name=None, tab_id=None, url=None, username=None, saved=()):
    """The login and the tab a fill acts on, and the saved Chrome login when the vault has none."""
    if name:
        meta = next((m for m in logins if m.get("name") == name), None)
        if meta is None:
            raise Refused("no login named %s is enrolled; `chrome-login.py list` shows them"
                          % name)
        return meta, choose_tab(tabs, login_hosts(meta), tab_id, url), None
    known = sorted({h for m in logins for h in login_hosts(m)} | {r["host"] for r in saved})
    if not known:
        raise Refused("neither the vault nor Chrome holds a login for any host")
    tab = choose_tab(tabs, known, tab_id, url)
    host = host_of(tab["url"])
    owners = [m for m in logins if host in login_hosts(m)
              and (not username or m.get("description") == username)]
    if len(owners) > 1:
        raise Refused("several logins sign in on %s; name one: %s"
                      % (host, ", ".join(m["name"] for m in owners)))
    if owners:
        return owners[0], tab, None
    rows = [r for r in saved if r["host"] == host and (not username or r["username"] == username)]
    if not rows:
        raise Refused("neither the vault nor Chrome holds a login for %s%s"
                      % (host, " as %s" % username if username else ""))
    row = rows[0]
    return {"name": None, "description": row["username"], "tags": ["host:%s" % host]}, tab, row


# ---------------------------------------------------------------- approval

def build_approver(force=False):
    source = KIT / "approve.swift"
    target = approver_path()
    if not force and target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["swiftc", "-swift-version", "5", "-O", "-o", str(target), str(source)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused("the approval prompt did not build: %s" % done.stderr.strip()[-300:], 4)
    return target


def approve(reason):
    """Touch ID, an Apple Watch or the login password, answered by the owner alone."""
    try:
        done = subprocess.run([str(build_approver()), reason], capture_output=True, text=True,
                              timeout=150)
    except subprocess.TimeoutExpired:
        return "timed out"
    return {0: "approved", 1: "denied", 4: "timed out"}.get(done.returncode, "unavailable")


# ---------------------------------------------------------------- the fill

def drive(chrome, tab, payload, submit=True, clock=time.monotonic, sleep=time.sleep, say=print):
    """Fill each step of the sign-in as the site shows it, each field once."""
    hosts = payload["hosts"]
    filled = []
    last_fill = None
    quiet = 0
    started = clock()
    while clock() - started < FLOW_LIMIT:
        try:
            answer = chrome.probe(tab, hosts)
        except (OSError, RuntimeError, ValueError):
            # Between a submit and the next page there is briefly no document to
            # read; a tab that is gone for good runs out the clock instead.
            sleep(1)
            continue
        if answer.get("refused"):
            if "password" in filled:
                return filled, "the tab moved on to %s" % (answer.get("host") or "another page")
            raise Refused("the tab is no longer on %s" % ", ".join(hosts))
        fields = fields_of(answer)
        # Once the password is in, a username-shaped field belongs to the site
        # rather than the sign-in: a search box, a newsletter, an invite form.
        if "password" in filled:
            fields["username"] = False
        if not any(fields.values()):
            quiet += 1
            if quiet >= 3:
                if not filled:
                    raise Refused("%s shows no sign-in fields" % answer.get("host"))
                return filled, "the sign-in fields are gone from %s" % answer.get("host")
            sleep(1)
            continue
        quiet = 0
        pending = [k for k in ("username", "password", "totp") if fields[k] and k not in filled]
        if not pending:
            # Every field showing was filled and submitted already. A field that
            # comes back is the site refusing it or a challenge in front of it, and
            # filling it again is how an account is walked into a lockout.
            if last_fill is not None and clock() - last_fill > STEP_LIMIT:
                raise Refused("%s still shows the %s field %ds after it was submitted; the "
                              "page holds an error or a challenge" % (
                                  answer.get("host"),
                                  "/".join(k for k, v in fields.items() if v), STEP_LIMIT))
            sleep(1)
            continue
        values = {}
        if "password" in pending:
            values["password"] = payload["password"]
            if "username" in pending:
                values["username"] = payload["username"]
        elif "username" in pending:
            values["username"] = payload["username"]
        if "totp" in pending and ("password" in values or not values):
            if not payload.get("totp"):
                raise Refused("%s asks for a one-time code this login does not hold"
                              % answer.get("host"))
            config = payload["totp"]
            left = config["period"] - time.time() % config["period"]
            if left < 3:
                sleep(left + 0.5)
            values["totp"] = totp_code(config)
        try:
            result = chrome.fill(tab, hosts, values, submit)
        except (OSError, RuntimeError, ValueError):
            sleep(1)
            continue
        if result.get("refused"):
            raise Refused("the tab is no longer on %s" % ", ".join(hosts))
        if any(f.get("submitted") == "held" for f in result.get("frames") or []):
            # The page's script had not taken the form over yet; nothing was sent.
            sleep(1)
            continue
        kinds = [k for k in ("username", "password", "totp")
                 if any(k in (f.get("filled") or []) for f in result.get("frames") or [])]
        missed = [k for k in values if k not in kinds]
        if missed:
            raise Refused("the %s field on %s would not take a value"
                          % ("/".join(missed), result.get("host")))
        filled.extend(k for k in kinds if k not in filled)
        say("filled %s on %s%s" % (", ".join(kinds), result.get("host"),
                                    "" if submit else "; not submitted"))
        if not submit:
            return filled, "left unsubmitted"
        last_fill = clock()
        sleep(2)
    raise Refused("the sign-in did not finish within %ds" % FLOW_LIMIT)


def fill(name=None, tab_id=None, url=None, submit=True, store=None, chrome=None,
         approver=approve, say=print, username=None, saved=chrome_saved, key=chrome_key,
         **timing):
    chrome = chrome or Chrome()
    if not chrome.relays():
        raise Refused("the Chrome Login extension is not connected; "
                      "`chrome-login.py status` says what is missing", 4)
    own = store is None
    store = store or Store()
    try:
        logins = store.logins()
        meta, tab, row = choose(logins, chrome.tabs(), name, tab_id, url, username,
                                () if name else saved())
        host = host_of(tab["url"])
        probe = chrome.probe(tab, login_hosts(meta), front=True)
        if probe.get("refused") or not any(fields_of(probe).values()):
            raise Refused("%s shows no sign-in fields" % host)
        account = meta.get("description") or meta["name"]
        verdict = approver("sign in to %s as %s in Chrome" % (host, account))
        if verdict != "approved":
            raise Refused("the sign-in to %s was %s" % (host, verdict), 3)
        if row is None:
            payload = store.secret(meta["name"])
        else:
            taken, payload = take_from_chrome(row, logins, store, key)
            say("copied the %s login for %s from Chrome into the vault as %s"
                % (account, host, taken))
            meta = dict(meta, name=taken)
    finally:
        if own:
            store.close()
    if host not in payload["hosts"]:
        raise Refused("login %s is not for %s" % (meta["name"], host))
    filled, outcome = drive(chrome, tab, payload, submit, say=say, **timing)
    say("done: %s as %s; %s" % (meta["name"], account, outcome))
    return filled


# ---------------------------------------------------------------- setup

def add(name, hosts=None, username=None, ask=input, secret=getpass.getpass, store=None,
        interactive=None):
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if not interactive:
        raise Refused("add reads the password from the keyboard; run it in your own terminal", 2)
    if not LOGIN_NAME.match(name):
        raise Refused("a login name is lowercase letters, digits, dots, dashes and underscores", 2)
    try:
        hosts = [normalize_host(h) for h in (hosts or ask(
            "Hosts it signs in on, comma-separated (github.com, www.github.com): ").split(","))
            if h.strip()]
    except ValueError as problem:
        raise Refused(str(problem), 2)
    if not hosts:
        raise Refused("a login needs at least one host", 2)
    username = (username or ask("Username or email: ")).strip()
    password = secret("Password: ")
    if not username or not password:
        raise Refused("a login needs a username and a password", 2)
    if secret("Password again: ") != password:
        raise Refused("the two passwords differ; nothing was stored", 2)
    seed = secret("One-time code seed or otpauth:// URI (Enter for none): ").strip()
    payload = {"v": 1, "hosts": list(dict.fromkeys(hosts)), "username": username,
               "password": password}
    if seed:
        try:
            payload["totp"] = parse_totp(seed)
        except ValueError as problem:
            raise Refused("the one-time code seed was refused: %s" % problem, 2)
    own = store is None
    store = store or Store()
    try:
        store.put(name, payload)
    finally:
        if own:
            store.close()
    return payload["hosts"]


def relay_manifest():
    return {"name": RELAY, "description": "Chrome Login relay for chrome-login.py",
            "path": str(KIT / "host.py"), "type": "stdio",
            "allowed_origins": ["chrome-extension://%s/" % EXTENSION_ID]}


def profiles_with_extension():
    names = []
    for prefs in sorted(CHROME.glob("*/Preferences")):
        profile = prefs.parent
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
            secure = json.loads((profile / "Secure Preferences").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        settings = dict(data.get("extensions", {}).get("settings", {}))
        settings.update(secure.get("extensions", {}).get("settings", {}))
        if EXTENSION_ID in settings:
            names.append(data.get("profile", {}).get("name") or profile.name)
    return names


def install(say=print):
    binary = build_approver(force=True)
    os.chmod(KIT / "host.py", 0o755)
    target = CHROME / "NativeMessagingHosts" / ("%s.json" % RELAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(relay_manifest(), indent=2) + "\n", encoding="utf-8")
    say("approval prompt: %s" % binary)
    say("relay registered: %s" % target)
    reloaded = 0
    for relay in Chrome().relays():
        try:
            Chrome().ask(relay, {"op": "reload"}, timeout=5)
            reloaded += 1
        except (OSError, RuntimeError, ValueError):
            pass
    if reloaded:
        say("extension reloaded in %d profile%s" % (reloaded, "" if reloaded == 1 else "s"))
    say("extension: load %s unpacked in each Chrome profile Claude signs in from"
        % (KIT / "extension"))


def status(chrome=None, say=print):
    chrome = chrome or Chrome()
    target = CHROME / "NativeMessagingHosts" / ("%s.json" % RELAY)
    rows = []
    binary = approver_path()
    ready = binary.is_file() and subprocess.run(
        [str(binary), "--check"], capture_output=True).returncode == 0
    rows.append(("approval prompt", ready, str(binary)))
    try:
        registered = json.loads(target.read_text(encoding="utf-8")) == relay_manifest()
    except (OSError, ValueError):
        registered = False
    rows.append(("relay registered", registered, str(target)))
    profiles = profiles_with_extension()
    rows.append(("extension loaded", bool(profiles), ", ".join(profiles) or "in no profile"))
    live = 0
    for relay in chrome.relays():
        try:
            chrome.ask(relay, {"op": "tabs"}, timeout=5)
            live += 1
        except (OSError, RuntimeError, ValueError):
            pass
    rows.append(("extension connected", live > 0, "%d profile%s" % (live, "" if live == 1 else "s")))
    for label, good, detail in rows:
        say("%-20s %-4s %s" % (label, "ok" if good else "no", detail))
    return all(good for _, good, _ in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chrome-login.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fill", help="fill and submit a sign-in in Chrome")
    p.add_argument("login", nargs="?")
    p.add_argument("--tab", type=int)
    p.add_argument("--url")
    p.add_argument("--username")
    p.add_argument("--no-submit", action="store_true")
    sub.add_parser("list", help="every enrolled login, without its secret")
    p = sub.add_parser("add", help="enrol or replace a login, from your own terminal")
    p.add_argument("login")
    p.add_argument("--host", action="append")
    p.add_argument("--username")
    sub.add_parser("install", help="build the approval prompt and register the relay")
    sub.add_parser("status", help="what is built, registered, loaded and connected")
    args = parser.parse_args(argv)
    try:
        if args.command == "fill":
            fill(args.login, args.tab, args.url, not args.no_submit, username=args.username)
        elif args.command == "list":
            store = Store()
            try:
                logins = store.logins()
            finally:
                store.close()
            for meta in logins:
                print("%-20s %-40s %s" % (meta["name"], ", ".join(login_hosts(meta)),
                                          meta.get("description") or ""))
            if not logins:
                print("no login is enrolled")
        elif args.command == "add":
            hosts = add(args.login, args.host, args.username)
            print("stored %s for %s" % (args.login, ", ".join(hosts)))
        elif args.command == "install":
            install()
        elif args.command == "status":
            return 0 if status() else 4
    except Refused as problem:
        print("refused: %s" % problem, file=sys.stderr)
        return problem.status
    except RuntimeError as problem:
        print("failed: %s" % problem, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
