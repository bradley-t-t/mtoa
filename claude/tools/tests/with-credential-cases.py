#!/usr/bin/env python3
"""Cases the credential wrapper has to get right.

The whole value of this tool is negative: a secret that reaches a command line,
a log, or a transcript has already leaked, and no later care recovers it. So the
cases pin the leak paths shut rather than only proving the happy path works - the
child sees the value, the parent's own output never does, and a credential the
vault will not hand over leaves the variable unset instead of empty-stringing it
into place where a caller would read it as present.

  with-credential-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "with-credential.py")

# A stand-in for whatever the vault holds. The cases assert that the child is
# handed it and that the parent never renders it, and neither reading turns on
# the value's shape. A key-shaped one here is a credential published in a
# repository every machine and every container reads, which is the shape the
# tool under test exists to keep off a command line.
SECRET = "fixture-value-not-a-credential-0000000"

# The vault cases below stand in for CryptoFort, so the backend is named for
# them rather than read from whatever profile the machine holds.
os.environ["MTOA_SECRETS_BACKEND"] = "cryptofort"
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
    """The vault as the tool uses it: one held name, everything else refused."""

    held = {"google-pagespeed-api-key": SECRET}
    closed = False

    def get(self, name):
        if name not in self.held:
            raise RuntimeError("the vault holds no %s" % name)
        return self.held[name]

    def close(self):
        FakeVault.closed = True


def run(mod, argv):
    """Drive main() with argv, returning its code and what it printed."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    sys.argv = ["with-credential.py"] + argv
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main()
    finally:
        sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def usage_cases(mod):
    code, _, err = run(mod, ["a=B", "echo", "hi"])
    check("no -- separator is refused", code, 2)
    check("the refusal says what the form is", "credential" in err.lower(), True)

    code, _, err = run(mod, ["--"])
    check("nothing after -- is refused", code, 2)

    code, _, err = run(mod, ["notapair", "--", "true"])
    check("a mapping without = is refused", code, 2)
    check("the bad mapping is named", "notapair" in err, True)


def injection_cases(mod):
    mod.Vault = FakeVault
    with tempfile.TemporaryDirectory() as tmp:
        seen = Path(tmp) / "seen.json"
        reader = Path(tmp) / "reader.py"
        reader.write_text(
            "import json,os,sys\n"
            "json.dump({k: os.environ.get(k) for k in ('GOOGLE_PAGESPEED_API_KEY','ABSENT')},\n"
            "          open(sys.argv[1],'w'))\n")

        code, out, err = run(mod, ["google-pagespeed-api-key=GOOGLE_PAGESPEED_API_KEY",
                                   "--", sys.executable, str(reader), str(seen)])
        env = json.loads(seen.read_text())
        check("the child ran", code, 0)
        check("the child sees the secret", env["GOOGLE_PAGESPEED_API_KEY"], SECRET)
        check("the parent never prints it", SECRET in out or SECRET in err, False)
        check("the session is closed after", FakeVault.closed, True)

        code, out, err = run(mod, ["no-such-credential=ABSENT",
                                   "--", sys.executable, str(reader), str(seen)])
        env = json.loads(seen.read_text())
        check("a missing credential still runs the command", code, 0)
        check("a missing credential leaves the variable unset", env["ABSENT"], None)
        check("the missing name is reported", "no-such-credential" in err, True)


def exit_code_cases(mod):
    mod.Vault = FakeVault
    code, _, _ = run(mod, ["--", sys.executable, "-c", "raise SystemExit(3)"])
    check("the child's exit code is the tool's", code, 3)
    code, _, _ = run(mod, ["--", sys.executable, "-c", "pass"])
    check("a clean child exits clean", code, 0)


def no_vault_without_mappings(mod):
    """A run with no credentials asked for must never open a vault session."""

    class Exploding:
        def __init__(self):
            raise AssertionError("the vault was opened with nothing to fetch")

    mod.Vault = Exploding
    code, _, _ = run(mod, ["--", sys.executable, "-c", "pass"])
    check("no mappings opens no session", code, 0)


def env_file_cases(mod):
    """The env-file store: read when locked to its owner, refused when it is not."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "secrets.env"
        path.write_text("# a comment\ngoogle-pagespeed-api-key=%s\nquoted='q v'\n" % SECRET)
        os.chmod(path, 0o600)
        saved = {k: os.environ.get(k) for k in ("MTOA_SECRETS_BACKEND", "MTOA_SECRETS_FILE")}
        try:
            os.environ["MTOA_SECRETS_BACKEND"] = "env"
            os.environ["MTOA_SECRETS_FILE"] = str(path)
            store = mod.open_backend()
            check("env: a held name is read", store.get("google-pagespeed-api-key"), SECRET)
            check("env: quotes are stripped", store.get("quoted"), "q v")
            try:
                store.get("absent")
                check("env: an absent name raises", False, True)
            except RuntimeError:
                pass
            out_buf, err_buf = io.StringIO(), io.StringIO()
            probe = Path(tmp) / "env.json"
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                sys.argv = ["with-credential.py", "google-pagespeed-api-key=KEY", "--",
                            sys.executable, "-c",
                            "import json,os;json.dump({'k':os.environ.get('KEY')},open(%r,'w'))"
                            % str(probe)]
                code = mod.main()
            check("env: the child ran", code, 0)
            check("env: the child sees the value", json.loads(probe.read_text())["k"], SECRET)
            check("env: the parent never prints it",
                  SECRET in out_buf.getvalue() or SECRET in err_buf.getvalue(), False)
            os.chmod(path, 0o644)
            try:
                mod.open_backend()
                check("env: a file others can read is refused", False, True)
            except RuntimeError:
                pass
            os.environ["MTOA_SECRETS_BACKEND"] = "nonsense"
            try:
                mod.open_backend()
                check("an unknown backend is refused", False, True)
            except RuntimeError:
                pass
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def main():
    mod = load()
    usage_cases(mod)
    injection_cases(mod)
    exit_code_cases(mod)
    no_vault_without_mappings(mod)
    env_file_cases(mod)
    os.environ["MTOA_SECRETS_BACKEND"] = "cryptofort"
    for row in FAILURES:
        print("FAIL " + row)
    print("%d case(s) failed." % len(FAILURES) if FAILURES else "All cases pass.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
