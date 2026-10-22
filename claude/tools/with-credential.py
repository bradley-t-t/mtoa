#!/usr/bin/env python3
"""Run a command with credentials in its environment, from wherever they are kept.

The vault is reachable only over the MCP server's stdio protocol, which leaves
every caller two bad options: read the secret into a transcript and paste it onto
a command line, or do without. Both have happened. A key that reaches a command
line reaches the shell history, the process table, and whatever log is watching,
and a checklist that does without files a gate as unread when the credential was
sitting in the vault the whole time.

This is the third option. The secret travels from the server into the child's
environment inside one process and is never rendered anywhere a person or a log
can see it.

    with-credential.py google-pagespeed-api-key=GOOGLE_PAGESPEED_API_KEY -- probe.py all

Each argument before `--` maps a credential name to the variable the child reads
it from. A credential the vault does not hold is named on stderr and left unset,
because a tool that reports the variable it wanted gives a better error than this
one could invent.

Where the values are kept is `secrets.backend` in the profile, or
MTOA_SECRETS_BACKEND for one run:

  cryptofort   the CryptoFort vault over its MCP server (secrets.cryptofort_launcher,
               ~/.claude/cryptofort-mcp.sh by default)
  env          a NAME=value file readable by its owner alone (secrets.env_file,
               ~/.config/mtoa/secrets.env by default)
  keychain     the macOS login Keychain, service `mtoa`, account = the name
  1password    the 1Password CLI, op://<secrets.op_vault, mtoa>/<name>/credential
"""

import json
import os
import subprocess
import sys
from pathlib import Path



def _profile_value(key, default=None):
    """One value from the owner's profile (mtoa_config.py), or the default."""
    import importlib.util
    for path in (Path(__file__).resolve().parent / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.get(key, default)
    return default


SERVER = str(Path(_profile_value("secrets.cryptofort_launcher")
                  or Path.home() / ".claude" / "cryptofort-mcp.sh").expanduser())


class Vault:
    """One MCP session, opened once and reused for every credential asked for."""

    def __init__(self):
        # This process is the one place a decrypted secret is allowed to exist:
        # it goes straight into a child's environment and is never rendered. The
        # read permission is asked for here rather than granted in the launcher,
        # so a session reaching the vault any other way cannot read a value at
        # all. The launcher puts --allow-write ahead of these arguments, so this
        # private session also carries put and purge; it calls neither.
        self.proc = subprocess.Popen(
            ["bash", SERVER, "--allow-secret-read"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.seq = 0
        self._call("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "with-credential", "version": "1"}})
        self._notify("notifications/initialized")

    def _send(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _call(self, method, params):
        self.seq += 1
        self._send({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("the vault closed before answering")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "unknown vault error"))
                return msg.get("result", {})

    def get(self, name):
        result = self._call("tools/call",
                            {"name": "credential_get", "arguments": {"name": name}})
        if result.get("isError"):
            raise RuntimeError("the vault holds no %s" % name)
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            # The server answers a name it does not hold with this text rather
            # than an error, and passed on it would stand in for the secret.
            if text.startswith("not found:"):
                raise RuntimeError("the vault holds no %s" % name)
            try:
                payload = json.loads(text)
            except ValueError:
                return text.strip()
            if isinstance(payload, dict):
                for field in ("secret", "value", "password", "token"):
                    if payload.get(field):
                        return str(payload[field])
            return text.strip()
        raise RuntimeError("the vault returned no value for %s" % name)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


class EnvFile:
    """NAME=value lines in a file only its owner can read."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("MTOA_SECRETS_FILE")
                         or _profile_value("secrets.env_file")
                         or Path.home() / ".config" / "mtoa" / "secrets.env").expanduser()
        if not self.path.is_file():
            raise RuntimeError("no secrets file at %s" % self.path)
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise RuntimeError("%s is readable by others; chmod 600 it" % self.path)
        self.values = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            self.values[name.strip()] = value

    def get(self, name):
        if name not in self.values:
            raise RuntimeError("%s holds no %s" % (self.path, name))
        return self.values[name]

    def close(self):
        self.values = {}


class Command:
    """A store read one value at a time through its own command line tool."""

    def __init__(self, argv_for, label):
        self.argv_for, self.label = argv_for, label

    def get(self, name):
        try:
            done = subprocess.run(self.argv_for(name), capture_output=True, text=True,
                                  timeout=30)
        except (OSError, subprocess.SubprocessError) as problem:
            raise RuntimeError("%s is not reachable (%s)" % (self.label, problem))
        if done.returncode != 0 or not done.stdout.strip():
            raise RuntimeError("%s holds no %s" % (self.label, name))
        return done.stdout.rstrip("\n")

    def close(self):
        pass


def open_backend():
    """The store the profile names, opened once for every credential asked for."""
    kind = (os.environ.get("MTOA_SECRETS_BACKEND")
            or _profile_value("secrets.backend") or "env").lower()
    if kind == "cryptofort":
        return Vault()
    if kind == "env":
        return EnvFile()
    if kind == "keychain":
        return Command(lambda name: ["security", "find-generic-password", "-s", "mtoa",
                                     "-a", name, "-w"], "the Keychain")
    if kind in ("1password", "op"):
        vault = _profile_value("secrets.op_vault") or "mtoa"
        return Command(lambda name: ["op", "read", "op://%s/%s/credential" % (vault, name)],
                       "1Password")
    raise RuntimeError("secrets.backend %r is not one of cryptofort, env, keychain, "
                       "1password" % kind)


def main():
    argv = sys.argv[1:]
    if "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    split = argv.index("--")
    mappings, command = argv[:split], argv[split + 1:]
    if not command:
        print("nothing to run after --", file=sys.stderr)
        return 2

    env = dict(os.environ)
    vault, missing = None, []
    try:
        for pair in mappings:
            name, _, var = pair.partition("=")
            if not name or not var:
                print("%r is not <credential-name>=<ENV_VAR>" % pair, file=sys.stderr)
                return 2
            if vault is None:
                try:
                    vault = open_backend()
                except RuntimeError as exc:
                    print("unset: every credential (%s)" % exc, file=sys.stderr)
                    return subprocess.run(command, env=env).returncode
            try:
                env[var] = vault.get(name)
            except RuntimeError as exc:
                missing.append("%s -> %s: %s" % (name, var, exc))
    finally:
        if vault is not None:
            vault.close()

    for row in missing:
        print("unset: %s" % row, file=sys.stderr)
    return subprocess.run(command, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
