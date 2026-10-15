#!/usr/bin/env python3
"""The one place a machine's own names live.

Who the owner is, which GitHub owners count as theirs, where the host is, what
the assistant is called: everything a tool, a guard, a host service or the voice
server needs to know about the person running it is read from one JSON file, so
the code carries no names of its own and installs anywhere.

The file is the first of these that exists:

  $MTOA_CONFIG                     an explicit path, which is how a case suite
                                   hands a tool its fixture
  ~/.claude/profile/mtoa.json      the profile the setup sync carries to every
                                   machine
  ~/.config/mtoa/mtoa.json         a host that holds no ~/.claude

A key the file does not set falls back to DEFAULTS, which name nobody. A value
that is still empty after that means the feature it drives is not set up here,
and the caller stands that feature down rather than guessing.

  mtoa_config.py get <key> [default]   print one value by dotted key; a list or
                                       an object prints as JSON, a missing or
                                       empty value prints the default or nothing
  mtoa_config.py path                  print the file in use, or nothing
  mtoa_config.py show                  print the merged settings as JSON
  mtoa_config.py render <file> [name]  print a prompt file with the profile's
                                       values filled in, and the passages the
                                       profile keeps for routine <name>

This file is copied verbatim beside the host services and the voice server, so
it imports nothing outside the standard library and nothing beside itself.
"""
import copy
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "user": {
        "name": "",
        "email": "",
        # The identity commits are made under on the host.
        "git_name": "",
        "git_email": "",
        # The words a prompt uses for the owner. A profile sets its own; these
        # read correctly for anyone.
        "pronouns": {"subject": "they", "object": "them", "possessive": "their"},
    },
    "assistant": {"name": "Sunday", "wake_word": "sunday"},
    "github": {
        # Owners whose repositories follow the develop/main workflow. Anything
        # else is somebody else's and is left alone.
        "owners": [],
        # The private repository the setup sync reconciles ~/.claude against.
        "mirror": "",
        # Where the host files tickets and the routines read them back.
        "tickets": "",
        # A repository whose deploys are watched under a different slug.
        "elsewhere": {},
    },
    "host": {
        # How this machine reaches the always-on host: an ssh alias, its public
        # URL, the account and home the services run under, and the port the
        # voice server listens on.
        "ssh": "",
        "url": "",
        "user": "",
        "home": "",
        "lan_name": "",
        "lan_ip": "",
        "tailnet_ip": "",
        "voice_port": 8794,
        # What the public status page calls everything behind the sites.
        "status_name": "Cloud Services",
    },
    # Outreach tables the attention board watches, where there are any.
    "outreach": {"supabase_url": ""},
    # Where routine mail goes out from, and the credential name its key is kept
    # under. It only ever goes to user.email.
    # The owner's own computer as the host reaches it (an ssh alias), and its home
    # directory, for the assistant's errands on it; and the session id of the standing
    # chat there that takes voice work, found by that id whatever it is titled.
    "desk": {"ssh": "", "home": "", "session": ""},
    "notify": {"slack_channel": "", "mail_from": "", "mail_credential": "resend-api-key"},
    "secrets": {"backend": "env"},
    # The studio or agency the owner builds sites under, when there is one: its
    # own site, the checkout that holds it, and the bar every client site carries.
    "agency": {},
    "desktop": {"keep_folders": []},
    # Words the voice says wrong as written: [{"name", "pattern", "say"}], where
    # `pattern` is a regular expression whose group `w` is the word.
    "voice": {"pronounce": []},
}

_CACHE = {}


def candidates():
    explicit = os.environ.get("MTOA_CONFIG")
    if explicit:
        return [Path(explicit).expanduser()]
    home = Path.home()
    return [home / ".claude" / "profile" / "mtoa.json",
            home / ".config" / "mtoa" / "mtoa.json"]


def path():
    """The settings file in use, or None where none exists."""
    for candidate in candidates():
        if candidate.is_file():
            return candidate
    return None


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load():
    """DEFAULTS with the settings file laid over them.

    Read again whenever the file changes, so a long-lived service picks up an
    edit without a restart. A file that does not parse is reported once on
    stderr and treated as absent: a typo in the profile must not take down a
    hook that runs on every tool call.
    """
    found = path()
    key = None
    if found is not None:
        try:
            key = (str(found), found.stat().st_mtime_ns)
        except OSError:
            found = None
    if key in _CACHE:
        return copy.deepcopy(_CACHE[key])
    merged = copy.deepcopy(DEFAULTS)
    if found is not None:
        try:
            data = json.loads(found.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _merge(merged, data)
        except (OSError, ValueError) as problem:
            sys.stderr.write("mtoa_config: %s is unreadable (%s); using defaults\n"
                             % (found, problem))
    _CACHE.clear()
    _CACHE[key] = merged
    return copy.deepcopy(merged)


def get(key, default=None):
    """One value by dotted key, or `default` where it is missing or empty."""
    node = load()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    if node in (None, "", [], {}):
        return default
    return node


def owners():
    """The GitHub owners as slug prefixes, ready for str.startswith."""
    return tuple(o.strip().rstrip("/") + "/" for o in (get("github.owners") or [])
                 if o and o.strip())


def render(text, extra=None):
    """Fill the profile's names into a prompt or a message.

    The placeholders are `{user}`, `{assistant}`, `{he}`, `{him}` and `{his}`;
    `{git_name}` and `{git_email}`, the identity commits are made under;
    `{tickets}` and `{mirror}`, the two repositories; `{host_ssh}`, `{host_lan}`,
    `{host_url}` and `{host_home}`; `{desk_ssh}`; `{agency}` and `{agency_domain}`; plus any key
    `extra` adds. Only those exact tokens are replaced, so braces that belong to
    code or JSON in the same text are left alone. Where the owner has no name
    set, `{user}` reads "the owner".
    """
    pron = get("user.pronouns") or {}
    values = {
        "user": get("user.name") or "the owner",
        "assistant": get("assistant.name") or "Sunday",
        "he": pron.get("subject") or "they",
        "him": pron.get("object") or "them",
        "his": pron.get("possessive") or "their",
        "git_name": get("user.git_name") or get("user.name") or "",
        "git_email": get("user.git_email") or get("user.email") or "",
        "tickets": get("github.tickets") or "",
        "mirror": get("github.mirror") or "",
        "host_ssh": get("host.ssh") or "",
        "host_lan": get("host.lan_name") or get("host.ssh") or "",
        "host_url": get("host.url") or "",
        "host_home": get("host.home") or str(Path.home()),
        "desk_ssh": get("desk.ssh") or "",
        "agency": get("agency.name") or "",
        "agency_domain": get("agency.domain") or "",
    }
    values.update(extra or {})
    for name, value in values.items():
        text = text.replace("{%s}" % name, str(value))
        cap = "{%s}" % (name[:1].upper() + name[1:])
        if cap in text:
            text = text.replace(cap, str(value)[:1].upper() + str(value)[1:])
    return text


def _print(value):
    if isinstance(value, (dict, list)):
        print(json.dumps(value))
    elif value is not None:
        print(value)


def main(argv):
    verb = argv[1] if len(argv) > 1 else ""
    if verb == "get" and len(argv) >= 3:
        _print(get(argv[2], argv[3] if len(argv) > 3 else None))
        return 0
    if verb == "path":
        found = path()
        if found:
            print(found)
        return 0 if found else 1
    if verb == "show":
        print(json.dumps(load(), indent=2))
        return 0
    if verb == "render" and len(argv) >= 3:
        # A routine's prompt as the routine reads it: the file with every
        # placeholder filled, plus that routine's own passages from the profile
        # (`routines.<name>`), each standing where its {placeholder} stands.
        try:
            text = Path(argv[2]).read_text(encoding="utf-8")
        except OSError as problem:
            sys.stderr.write("mtoa_config: cannot read %s (%s)\n" % (argv[2], problem))
            return 1
        own = get("routines.%s" % argv[3]) if len(argv) > 3 else None
        extra = {k: v for k, v in (own or {}).items() if isinstance(v, str)}
        sys.stdout.write(render(text, extra))
        return 0
    sys.stderr.write(__doc__.split("\n\n", 4)[-1] + "\n")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
