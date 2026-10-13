#!/usr/bin/env python3
"""Cases the profile reader has to get right.

Every tool, guard, host service and the voice server reads the owner's names
through this one module, so what it resolves, what it falls back to and what it
fills into a prompt are pinned here once.

  mtoa_config-cases.py        run them
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "mtoa_config.py")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    saved = {k: os.environ.get(k) for k in ("MTOA_CONFIG", "HOME")}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            home = Path(tmp) / "home"
            (home / ".claude" / "profile").mkdir(parents=True)
            (home / ".config" / "mtoa").mkdir(parents=True)
            os.environ["HOME"] = str(home)
            os.environ.pop("MTOA_CONFIG", None)
            mod = load()

            check("no file: path is None", mod.path(), None)
            check("no file: owners empty", mod.owners(), ())
            check("no file: default stands", mod.get("secrets.backend"), "env")
            check("no file: empty value gives the default", mod.get("host.url", "x"), "x")
            check("no file: assistant default", mod.get("assistant.name"), "Sunday")
            check("no file: render names nobody",
                  mod.render("{user} said {he} would"), "the owner said they would")

            fallback = home / ".config" / "mtoa" / "mtoa.json"
            fallback.write_text(json.dumps({"host": {"url": "https://fallback"}}))
            check("host-only file is read", mod.get("host.url"), "https://fallback")

            profile = home / ".claude" / "profile" / "mtoa.json"
            profile.write_text(json.dumps({
                "user": {"name": "Ada", "pronouns": {"subject": "she", "object": "her",
                                                     "possessive": "her"}},
                "github": {"owners": ["acme", "acme-org/"]},
                "host": {"url": "https://host.example"},
            }))
            check("profile outranks the host file", mod.get("host.url"), "https://host.example")
            check("profile path", mod.path(), profile)
            check("owners become prefixes", mod.owners(), ("acme/", "acme-org/"))
            check("merge keeps defaults beside a set key",
                  mod.get("host.voice_port"), 8794)
            check("render fills names and pronouns",
                  mod.render("{Assistant} tells {him} {he} has {his} {user} {x}", {"x": 1}),
                  "Sunday tells her she has her Ada 1")
            check("render leaves other braces", mod.render('{"a": {user}}'), '{"a": Ada}')
            check("render capitalises a capital placeholder",
                  mod.render("{He} went."), "She went.")

            explicit = Path(tmp) / "fixture.json"
            explicit.write_text(json.dumps({"github": {"owners": ["only"]}}))
            os.environ["MTOA_CONFIG"] = str(explicit)
            check("MTOA_CONFIG outranks every other file", mod.owners(), ("only/",))
            explicit.write_text(json.dumps({"github": {"owners": ["changed"]}}))
            os.utime(explicit, ns=(10**18, 10**18))
            check("an edited file is read again", mod.owners(), ("changed/",))

            explicit.write_text("{not json")
            os.utime(explicit, ns=(2 * 10**18, 2 * 10**18))
            check("a broken file falls back to defaults", mod.owners(), ())

            explicit.write_text(json.dumps({"host": {"url": "https://cli"},
                                            "github": {"owners": ["a"]}}))
            os.utime(explicit, ns=(3 * 10**18, 3 * 10**18))
            run = lambda *a: subprocess.run([sys.executable, str(TARGET), *a],
                                            capture_output=True, text=True)
            check("cli get", run("get", "host.url").stdout.strip(), "https://cli")
            check("cli list prints json", run("get", "github.owners").stdout.strip(), '["a"]')
            check("cli default", run("get", "host.ssh", "none").stdout.strip(), "none")
            check("cli path", run("path").stdout.strip(), str(explicit))
            check("cli usage exits 64", run().returncode, 64)
            prompt = Path(tmp) / "prompt.md"
            prompt.write_text("Queue {tickets}. {note}End {git_name}.")
            explicit.write_text(json.dumps({"github": {"tickets": "acme/queue"},
                                            "user": {"name": "Ada", "git_name": "Ada L"},
                                            "routines": {"r": {"note": "Mind X. "}}}))
            os.utime(explicit, ns=(4 * 10**18, 4 * 10**18))
            check("cli render fills values and the routine's passages",
                  run("render", str(prompt), "r").stdout, "Queue acme/queue. Mind X. End Ada L.")
            check("cli render without a routine leaves its passage token",
                  run("render", str(prompt)).stdout, "Queue acme/queue. {note}End Ada L.")
            check("cli render of a missing file fails", run("render", "/no/such").returncode, 1)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    for line in FAILURES:
        print("FAIL " + line)
    print("%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
