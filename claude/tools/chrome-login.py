#!/usr/bin/env python3
"""Sign a session into an account in Chrome without the password reaching it.

A session driving Chrome meets a sign-in page. Typing the password would write
it into the transcript, and the transcript outlives the sign-in. So the session
never holds it: it presses ctrl+alt+shift+L in the page, and the CryptoFort
Login extension (chrome-login/extension) asks this script, running as Chrome's
native messaging host, for the login bound to that exact origin. The script
reads it from the vault, raises the system authentication prompt naming the
site and the account, and only once that is approved hands it to the extension,
which fills the fields and submits. The session sees the outcome and nothing
else.

  chrome-login.py install          build the approval prompt, register the host with Chrome
  chrome-login.py check            say whether every piece is in place
  chrome-login.py list [host]      the logins the vault holds, metadata only
  chrome-login.py import <csv>     load a Chrome password export into the vault
  chrome-login.py host <origin>    the native messaging host; Chrome runs this, nothing else

A login lives in the vault's `logins` namespace, named <host>:<username>, and
its sealed value is JSON:

  {"origins": ["https://github.com"], "username": "...", "password": "...",
   "totp": "<base32 seed>", "totp_digits": 6, "totp_period": 30}

The TOTP fields are optional. The origins are inside the sealed value rather
than in the tags because a tag is writable by any session holding the vault's
write permission, and an origin that could be rewritten would let a page on
another site ask for the password. The tags (`login`, `host:<hostname>`,
`user:<username>`) only narrow which entries are opened, and name the account in
a listing.
"""

import base64
import binascii
import csv
import ctypes
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "chrome-login"
EXTENSION = ASSETS / "extension"
HOST_NAME = "cryptofort.chrome_login"
# Pinned by the public key in the extension's manifest, so an unpacked copy
# loaded from any directory on any machine carries the same ID and the host
# manifest written by `install` admits it.
EXTENSION_ID = "nmfnlmblcidnpkbenhpcphppfidehkbg"
CALLER = "chrome-extension://%s/" % EXTENSION_ID
NAMESPACE = "logins"
SUPPORT = Path.home() / "Library" / "Application Support" / "CryptoFort Login"
APPROVER = SUPPORT / "CryptoFort"
LAUNCHER = SUPPORT / "host"
CHROME_HOSTS = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
CHROME_PROFILES = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
# Google's Apple developer team. A process passes as Chrome only when the
# binary it runs is signed by it, so a script named "Google Chrome" does not.
CHROME_REQUIREMENT = 'anchor apple generic and certificate leaf[subject.OU] = "EQHXZ8M8AV"'
APPROVAL_TIMEOUT = 90


# --- origins -----------------------------------------------------------------

def origin_of(url):
    """scheme://host[:port] for an http(s) URL, lowercased, or None."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if port is None or port == {"http": 80, "https": 443}[parts.scheme]:
        return "%s://%s" % (parts.scheme, host)
    return "%s://%s:%d" % (parts.scheme, host, port)


def pattern_of(text):
    """A sealed origin as written: an origin, or https://*.example.com."""
    text = (text or "").strip().rstrip("/").lower()
    scheme, sep, rest = text.partition("://*.")
    if sep:
        if scheme in ("http", "https") and rest and "/" not in rest and ":" not in rest \
                and "." in rest:
            return text
        return None
    return origin_of(text)


def origin_matches(pattern, origin):
    """Whether a sealed origin admits the page's origin.

    Exact, or https://*.example.com, which admits any subdomain of example.com
    over the same scheme and default port, but not example.com itself.
    """
    if pattern == origin:
        return True
    scheme, sep, suffix = pattern.partition("://*.")
    if not sep:
        return False
    page_scheme, _, page_host = origin.partition("://")
    return page_scheme == scheme and page_host.endswith("." + suffix)


def hostname(origin):
    return origin.partition("://")[2].split(":")[0]


def tag_for(pattern):
    return "host:" + hostname(pattern)


def lookup_tags(origin):
    """Every host tag a login admitting this origin could carry."""
    host = hostname(origin)
    labels = host.split(".")
    return ["host:" + host] + ["host:*." + ".".join(labels[i:])
                               for i in range(1, len(labels) - 1)]


# --- records -----------------------------------------------------------------

def normalize_totp(value):
    """(seed, digits, period) from a base32 seed or an otpauth:// URI.

    Raises ValueError for anything a code cannot be made from, so a bad seed is
    refused when it is saved rather than discovered at the second factor.
    """
    value = (value or "").strip()
    digits, period = 6, 30
    if value.lower().startswith("otpauth://"):
        query = parse_qs(urlsplit(value).query)
        algorithm = (query.get("algorithm", ["SHA1"])[0] or "SHA1").upper()
        if algorithm != "SHA1":
            raise ValueError("only SHA1 one-time codes are supported, not %s" % algorithm)
        digits = int(query.get("digits", ["6"])[0])
        period = int(query.get("period", ["30"])[0])
        value = unquote(query.get("secret", [""])[0])
    seed = "".join(value.split()).replace("-", "").upper().rstrip("=")
    if not seed:
        raise ValueError("the one-time code seed is empty")
    try:
        base64.b32decode(seed + "=" * (-len(seed) % 8))
    except (binascii.Error, ValueError):
        raise ValueError("the one-time code seed is not base32")
    if digits not in (6, 7, 8) or not 10 <= period <= 120:
        raise ValueError("one-time codes of %d digits every %ds are not supported"
                         % (digits, period))
    return seed, digits, period


def make_record(origins, username, password, totp=None):
    """The sealed JSON of a login, validated; raises ValueError."""
    patterns = []
    for text in origins:
        pattern = pattern_of(text)
        if not pattern:
            raise ValueError("%r is not an http(s) origin" % text)
        if pattern not in patterns:
            patterns.append(pattern)
    if not patterns:
        raise ValueError("a login needs the origin it signs in to")
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("a login needs a username and a password")
    record = {"origins": patterns, "username": username, "password": password}
    if totp:
        record["totp"], record["totp_digits"], record["totp_period"] = normalize_totp(totp)
    return record


def parse_record(text):
    """A sealed login as a dict, or None where the value is not one."""
    try:
        record = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    origins = record.get("origins")
    if not isinstance(origins, list) or not origins \
            or not all(isinstance(o, str) for o in origins):
        return None
    if not isinstance(record.get("username"), str) or not isinstance(record.get("password"), str):
        return None
    return record


def entry_name(record):
    return "%s:%s" % (hostname(record["origins"][0]), record["username"])


def entry_tags(record):
    tags = ["login"]
    for pattern in record["origins"]:
        if tag_for(pattern) not in tags:
            tags.append(tag_for(pattern))
    return tags + ["user:" + record["username"]]


def logins_from_csv(rows):
    """(records, skipped) from the rows of a Chrome password export.

    Chrome writes name,url,username,password,note. A row for an Android app,
    or one without a username or password, has nothing to fill and is skipped.
    Two rows for the same site and username collapse to the later one, which is
    what the vault would keep anyway.
    """
    records, skipped = {}, {}
    for row in rows:
        origin = origin_of(row.get("url") or "")
        if not origin:
            skipped["not a web address"] = skipped.get("not a web address", 0) + 1
            continue
        try:
            record = make_record([origin], row.get("username"), row.get("password") or "")
        except ValueError:
            skipped["no username or password"] = skipped.get("no username or password", 0) + 1
            continue
        records[entry_name(record)] = record
    return list(records.values()), skipped


# --- the vault ---------------------------------------------------------------

def open_vault():
    """A CryptoFort session that may read secrets, through with-credential.py."""
    path = HERE / "with-credential.py"
    if not path.is_file():
        path = Path.home() / ".claude" / "tools" / "with-credential.py"
    spec = importlib.util.spec_from_file_location("with_credential", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Vault()


def call(vault, tool, arguments):
    result = vault._call("tools/call", {"name": tool, "arguments": arguments})
    text = "".join(block.get("text", "") for block in result.get("content", [])
                   if block.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text.strip() or "the vault refused %s" % tool)
    return text


def list_logins(vault):
    try:
        entries = json.loads(call(vault, "credential_list", {"namespace": NAMESPACE}))
    except ValueError:
        raise RuntimeError("the vault answered the listing with something other than JSON")
    return [e for e in entries if "login" in (e.get("tags") or [])]


def read_login(vault, name):
    return parse_record(call(vault, "credential_get", {"name": name, "namespace": NAMESPACE}))


def store_login(vault, record):
    origins = ", ".join(record["origins"])
    call(vault, "credential_put", {
        "name": entry_name(record),
        "namespace": NAMESPACE,
        "secret": json.dumps(record),
        "provider": hostname(record["origins"][0]),
        "tags": entry_tags(record),
        "description": "Sign-in for %s on %s, filled into Chrome by the CryptoFort Login "
                       "extension after Touch ID approval.%s"
                       % (record["username"], origins,
                          " Carries a one-time code seed." if record.get("totp") else ""),
    })


def usernames(entry):
    return [t[len("user:"):] for t in entry.get("tags") or [] if t.startswith("user:")]


# --- approval ----------------------------------------------------------------

def approve(reason):
    """Whether the person at the machine approved, through the system prompt."""
    if not os.access(str(APPROVER), os.X_OK):
        raise RuntimeError("the approval prompt is not built; run chrome-login.py install")
    try:
        done = subprocess.run([str(APPROVER), reason, str(APPROVAL_TIMEOUT)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=APPROVAL_TIMEOUT + 15)
    except subprocess.TimeoutExpired:
        return False
    if done.returncode == 2:
        raise RuntimeError("this Mac has no way to ask for approval")
    return done.returncode == 0


# --- the host ----------------------------------------------------------------

def handle(request, vault_factory=open_vault, approver=approve):
    """One request from the extension, answered as a dict."""
    action = request.get("action")
    origin = origin_of(request.get("origin") or "")
    if action in ("fill", "list") and not origin:
        return {"ok": False, "error": "only an http or https page has a login"}

    if action == "save":
        try:
            record = make_record([origin or ""] + list(request.get("extraOrigins") or []),
                                 request.get("username"), request.get("password"),
                                 request.get("totp") or None)
        except ValueError as problem:
            return {"ok": False, "error": str(problem)}
        vault = vault_factory()
        try:
            store_login(vault, record)
        finally:
            vault.close()
        return {"ok": True, "entry": entry_name(record), "origins": record["origins"]}

    if action == "list":
        wanted = set(lookup_tags(origin))
        vault = vault_factory()
        try:
            entries = list_logins(vault)
        finally:
            vault.close()
        return {"ok": True, "logins": [
            {"entry": e["name"], "usernames": usernames(e)}
            for e in entries if wanted & set(e.get("tags") or [])]}

    if action != "fill":
        return {"ok": False, "error": "unknown request %r" % action}

    account = (request.get("account") or "").strip()
    wanted = set(lookup_tags(origin))
    matches = []
    vault = vault_factory()
    try:
        for entry in list_logins(vault):
            if not wanted & set(entry.get("tags") or []):
                continue
            if account and account not in usernames(entry):
                continue
            record = read_login(vault, entry["name"])
            # The tags chose which entries to open; the sealed origins decide.
            if record and any(origin_matches(p, origin) for p in record["origins"]):
                matches.append((entry["name"], record))
    finally:
        vault.close()
    if not matches:
        return {"ok": False, "error": "the vault holds no login for %s%s"
                % (origin, " as %s" % account if account else "")}
    if len(matches) > 1:
        return {"ok": False, "accounts": [r["username"] for _, r in matches],
                "error": "the vault holds several logins for %s; set data-cryptofort-account "
                         "on <html> to the one wanted" % origin}
    name, record = matches[0]
    if not approver("sign in to %s as %s" % (hostname(origin), record["username"])):
        return {"ok": False, "error": "the sign-in was not approved"}
    reply = {"ok": True, "entry": name, "username": record["username"],
             "password": record["password"]}
    if record.get("totp"):
        reply["totp"] = {"seed": record["totp"], "digits": record.get("totp_digits", 6),
                         "period": record.get("totp_period", 30)}
    return reply


def executable_of(pid):
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        return None
    buffer = ctypes.create_string_buffer(4096)
    length = libproc.proc_pidpath(pid, buffer, ctypes.sizeof(buffer))
    return buffer.raw[:length].decode("utf-8", "replace") if length > 0 else None


def parent_of(pid):
    try:
        out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) if out else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def started_by_chrome():
    """Whether an ancestor of this process is a Chrome binary Google signed.

    Chrome starts a native messaging host itself. A host whose ancestry never
    reaches Chrome was started by a shell or a session, and the password it
    would write to stdout would land wherever that caller reads, which is how a
    password reaches a transcript.
    """
    pid, hops = os.getppid(), 0
    while pid > 1 and hops < 32:
        path = executable_of(pid)
        if path and path.endswith(".app/Contents/MacOS/Google Chrome"):
            # Checked against the running process rather than the file on disk,
            # so what is judged is the code that actually started this host.
            check = subprocess.run(["/usr/bin/codesign", "--verify", "-R=" + CHROME_REQUIREMENT,
                                    str(pid)], capture_output=True, timeout=30)
            return check.returncode == 0
        pid, hops = parent_of(pid), hops + 1
    return False


def read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    (size,) = struct.unpack("=I", header)
    return json.loads(stream.read(size).decode("utf-8"))


def write_message(stream, payload):
    data = json.dumps(payload).encode("utf-8")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


def run_host(argv):
    if not argv or argv[0] != CALLER or not started_by_chrome():
        sys.stderr.write("chrome-login host: refused; only Chrome, for the CryptoFort Login "
                         "extension, starts the host\n")
        return 2
    request = read_message(sys.stdin.buffer)
    if not isinstance(request, dict):
        return 1
    try:
        reply = handle(request)
    except Exception as problem:  # the extension shows the reason in the page
        reply = {"ok": False, "error": "the vault could not be read: %s" % problem}
    write_message(sys.stdout.buffer, reply)
    return 0


# --- install and check -------------------------------------------------------

def host_manifest():
    return {"name": HOST_NAME,
            "description": "CryptoFort Login: fills a sign-in from the CryptoFort vault",
            "path": str(LAUNCHER), "type": "stdio", "allowed_origins": [CALLER]}


def install():
    SUPPORT.mkdir(parents=True, exist_ok=True)
    os.chmod(str(SUPPORT), 0o700)
    built = subprocess.run(["/usr/bin/swiftc", "-O", "-o", str(APPROVER),
                            str(ASSETS / "approve.swift")], capture_output=True, text=True)
    if built.returncode != 0:
        print("could not build the approval prompt:\n" + built.stderr.strip(), file=sys.stderr)
        return 1
    # Chrome starts the host with a bare PATH, so the interpreter is named in
    # full: the one on PATH here, a link that survives a Python upgrade, rather
    # than the versioned binary this run happens to be.
    python = shutil.which("python3") or sys.executable
    LAUNCHER.write_text('#!/bin/sh\nexec "%s" "%s" host "$@"\n' % (python, HERE / "chrome-login.py"))
    os.chmod(str(LAUNCHER), 0o700)
    CHROME_HOSTS.mkdir(parents=True, exist_ok=True)
    (CHROME_HOSTS / (HOST_NAME + ".json")).write_text(json.dumps(host_manifest(), indent=2) + "\n")
    print("approval prompt  %s" % APPROVER)
    print("host launcher    %s" % LAUNCHER)
    print("host manifest    %s" % (CHROME_HOSTS / (HOST_NAME + ".json")))
    print("extension        %s (id %s)" % (EXTENSION, EXTENSION_ID))
    print("Load the extension once per Chrome profile: chrome://extensions, Developer mode, "
          "Load unpacked, choose the extension folder.")
    return 0


def extension_id_from_manifest():
    import hashlib
    key = json.loads((EXTENSION / "manifest.json").read_text()).get("key", "")
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def profiles_with_extension():
    found = []
    for prefs in sorted(CHROME_PROFILES.glob("*/Secure Preferences")):
        try:
            settings = json.loads(prefs.read_text()).get("extensions", {}).get("settings", {})
        except (OSError, ValueError):
            continue
        if EXTENSION_ID in settings and not settings[EXTENSION_ID].get("disable_reasons"):
            found.append(prefs.parent.name)
    return found


def check():
    problems = []

    def line(ok, text):
        print("%s  %s" % ("ok     " if ok else "MISSING", text))
        if not ok:
            problems.append(text)

    line(os.access(str(APPROVER), os.X_OK), "approval prompt at %s" % APPROVER)
    line(LAUNCHER.is_file() and str(HERE / "chrome-login.py") in LAUNCHER.read_text(),
         "host launcher at %s" % LAUNCHER)
    manifest = CHROME_HOSTS / (HOST_NAME + ".json")
    try:
        registered = json.loads(manifest.read_text()) == host_manifest()
    except (OSError, ValueError):
        registered = False
    line(registered, "host registered with Chrome at %s" % manifest)
    line(extension_id_from_manifest() == EXTENSION_ID, "extension key pins id %s" % EXTENSION_ID)
    profiles = profiles_with_extension()
    line(bool(profiles), "extension loaded in Chrome (%s)"
         % (", ".join(profiles) if profiles else "no profile"))
    try:
        vault = open_vault()
        try:
            count = len(list_logins(vault))
        finally:
            vault.close()
        line(True, "vault reachable, %d login%s held" % (count, "" if count == 1 else "s"))
    except Exception as problem:
        line(False, "vault reachable (%s)" % problem)
    return 1 if problems else 0


# --- the other commands ------------------------------------------------------

def list_command(host_filter=None):
    vault = open_vault()
    try:
        entries = list_logins(vault)
    finally:
        vault.close()
    if host_filter:
        wanted = set(lookup_tags(origin_of(host_filter) or origin_of("https://" + host_filter) or ""))
        entries = [e for e in entries if wanted & set(e.get("tags") or [])]
    for entry in entries:
        hosts = [t[len("host:"):] for t in entry.get("tags") or [] if t.startswith("host:")]
        print("%-40s %-32s %s" % (entry["name"], ", ".join(usernames(entry)), ", ".join(hosts)))
    print("%d login%s" % (len(entries), "" if len(entries) == 1 else "s"))
    return 0


def import_command(path, dry_run=False):
    with open(os.path.expanduser(path), newline="", encoding="utf-8-sig") as handle:
        records, skipped = logins_from_csv(csv.DictReader(handle))
    if not dry_run and records:
        vault = open_vault()
        try:
            for record in records:
                store_login(vault, record)
        finally:
            vault.close()
    print("%s %d login%s" % ("would import" if dry_run else "imported", len(records),
                             "" if len(records) == 1 else "s"))
    for reason, count in sorted(skipped.items()):
        print("skipped %d: %s" % (count, reason))
    if not dry_run:
        print("The export holds every password in plain text; delete %s now." % path)
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    command, rest = argv[0], argv[1:]
    if command == "host":
        return run_host(rest)
    if command == "install":
        return install()
    if command == "check":
        return check()
    if command == "list":
        return list_command(rest[0] if rest else None)
    if command == "import" and rest:
        return import_command(rest[0], dry_run="--dry-run" in rest[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
