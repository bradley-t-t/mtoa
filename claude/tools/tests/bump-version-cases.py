#!/usr/bin/env python3
"""Cases the version bump has to get right.

A repo's own release tool outranks the generic step, the generic step moves
exactly one manifest, and the metadata that shadows the version — release.json
and the README badge — ends the run agreeing with it. The pom and gradle paths
take a single patch step whatever the command line says, since only npm is
handed the step name; that asymmetry is pinned here rather than smoothed over.

  bump-version-cases.py        run them
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "bump-version.py")

FAILURES = []


def calver_now():
    """The version bump-version.py writes for a manifest not already on this week."""
    year, week, _ = datetime.date.today().isocalendar()
    return "%d.%d.0" % (year, week)


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def bump(tmp, *args):
    """Run the copy under test inside the fixture directory."""
    # The tool bumps whatever sits in its working directory, so the working
    # directory is the whole containment story: it must be the temp tree and
    # never anywhere near the live config.
    assert str(tmp).startswith(tempfile.gettempdir()), f"fixture outside temp: {tmp}"
    assert not str(TARGET.parent).startswith(str(tmp)), f"tool inside fixture: {tmp}"
    return subprocess.run([sys.executable, str(TARGET), *args],
                          cwd=tmp, capture_output=True, text=True)


def write(tmp, rel, body):
    path = Path(tmp) / rel
    assert str(path).startswith(str(tmp)), f"escaped the temp tree: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def pkg(version):
    return json.dumps({"name": "fixture", "version": version}) + "\n"


def pkg_version(tmp):
    return json.loads((Path(tmp) / "package.json").read_text())["version"]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        p = bump(tmp)
        check("empty dir exits clean", p.returncode, 0)
        check("empty dir says so", p.stdout.strip(),
              "no version manifest here; nothing to bump")

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        p = bump(tmp)
        check("npm patch exits clean", p.returncode, 0)
        check("npm bump moves the manifest to CalVer", pkg_version(tmp), calver_now())
        check("npm bump reports the move", p.stdout.strip(), "1.2.3 -> " + calver_now())

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        p = bump(tmp, "minor")
        check("npm minor exits clean", p.returncode, 0)
        check("a semantic step name does not change the CalVer shape", pkg_version(tmp), calver_now())

    # A repo with its own release tool gets that tool, and the step name on the
    # command line never reaches it.
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        write(tmp, "scripts/calver.js",
              'const fs = require("fs");\n'
              'const pkg = JSON.parse(fs.readFileSync("package.json"));\n'
              'pkg.version = "2026.8.26";\n'
              'fs.writeFileSync("package.json", JSON.stringify(pkg) + "\\n");\n')
        p = bump(tmp, "major")
        check("calver exits clean", p.returncode, 0)
        check("calver outranks the step", pkg_version(tmp), "2026.8.26")
        check("calver reports the move", p.stdout.strip(), "1.2.3 -> 2026.8.26")

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        write(tmp, "scripts/calver.js",
              'console.error("release tool broken"); process.exit(1);\n')
        p = bump(tmp)
        check("broken release tool refuses", p.returncode, 1)
        check("broken release tool is named",
              "node scripts/calver.js failed" in p.stderr, True)
        check("broken release tool leaves the manifest", pkg_version(tmp), "1.2.3")

    # The pom path is a single patch step on the project's own version; the
    # step argument is npm-only, and a dependency's version never moves.
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "pom.xml",
              "<project><version>1.2.9</version>"
              "<dependencies><dependency><version>3.0.0</version>"
              "</dependency></dependencies></project>\n")
        p = bump(tmp, "major")
        check("pom exits clean", p.returncode, 0)
        text = (Path(tmp) / "pom.xml").read_text()
        check("pom takes one patch step", "<version>1.2.10</version>" in text, True)
        check("pom dependency untouched", "<version>3.0.0</version>" in text, True)
        check("pom reports the move", p.stdout.strip(), "1.2.9 -> 1.2.10")

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "gradle.properties", "org.gradle.jvmargs=-Xmx2g\nversion=0.9.9\n")
        p = bump(tmp)
        check("gradle exits clean", p.returncode, 0)
        text = (Path(tmp) / "gradle.properties").read_text()
        check("gradle takes one patch step", "version=0.9.10" in text, True)
        check("gradle keeps the other keys", "org.gradle.jvmargs=-Xmx2g" in text, True)
        check("gradle reports the move", p.stdout.strip(), "0.9.9 -> 0.9.10")

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        write(tmp, "pom.xml", "<project><version>7.7.7</version></project>\n")
        p = bump(tmp)
        check("package.json outranks pom", pkg_version(tmp), calver_now())
        check("pom left alone beside package.json",
              "<version>7.7.7</version>" in (Path(tmp) / "pom.xml").read_text(), True)

    # Release metadata that shadows the version is brought into step, keeping
    # its other fields, and a copy already in step is left byte-for-byte alone.
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        write(tmp, "public/release.json", '{"version": "1.2.3", "name": "fixture"}')
        p = bump(tmp)
        release = json.loads((Path(tmp) / "public" / "release.json").read_text())
        check("release.json follows the bump", release["version"], calver_now())
        check("release.json keeps its fields", release["name"], "fixture")

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        already = '{"version": "%s"}' % calver_now()
        write(tmp, "public/release.json", already)
        bump(tmp)
        check("release.json in step is untouched",
              (Path(tmp) / "public" / "release.json").read_text(), already)

    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "gradle.properties", "version=0.9.9\n")
        stale = '{"version": "0.0.1"}'
        write(tmp, "public/release.json", stale)
        bump(tmp)
        check("release.json is npm-only",
              (Path(tmp) / "public" / "release.json").read_text(), stale)

    # The README badge follows the bump through the sibling badge tool; a copy
    # under test sitting without that sibling leaves the badge where it was.
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "package.json", pkg("1.2.3"))
        write(tmp, "README.md",
              "![Version](https://img.shields.io/badge/version-1.2.3-blue)\n")
        p = bump(tmp)
        check("badge sync exits clean", p.returncode, 0)
        text = (Path(tmp) / "README.md").read_text()
        expected = ("version-%s-" % calver_now() if (TARGET.parent / "version-badge.py").exists()
                    else "version-1.2.3-")
        check("badge follows the bump", expected in text, True)

    for f in FAILURES:
        print(f"FAIL {f}")
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
