#!/usr/bin/env python3
"""Cases the note tool has to get right.

The value of a note is that it survives — the day it was written, the machine it
was written on, and the sync that carries it somewhere else. So these pin
survival rather than the happy path: two notes in one minute both keep, a second
machine's file does not shadow the first, and a day is read by merging every
file rather than by picking one.

The filename shape is load-bearing and is checked directly. `config-sync.py`
settles a contested file by last edit wins, so a note tool that ever wrote two
machines into one file would lose half a day's notes to a sync nobody watched.

  note-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "note.py")

passed = failed = 0


def check(what, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"pass  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}\n        got  {got!r}\n        want {want!r}")


def check_true(what, got):
    check(what, bool(got), True)


def load(home):
    """The tool under test, pointed at a throwaway ~/.claude."""
    os.environ["CLAUDE_CONFIG_DIR"] = str(home)
    spec = importlib.util.spec_from_file_location(f"note_{id(home)}", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    with tempfile.TemporaryDirectory() as work:
        home = Path(work) / ".claude"
        note = load(home)
        day = datetime(2026, 9, 4, 14, 30).astimezone()

        note.add("the first thing", when=day)
        note.add("the second thing", when=day)
        read = note.read_day("2026-09-04")
        check("two notes in one minute both keep", len(read), 2)
        check("and in the order they were written",
              [n["text"] for n in read], ["the first thing", "the second thing"])

        # The filename is what keeps two machines apart. Written by hand here
        # because the case cannot rename the machine it runs on.
        other = note.NOTES / "2026-09-04.otherbox.md"
        other.write_text("# Friday, September 4 2026 on otherbox\n\n"
                         "## 9:15 am\nwritten somewhere else\n\n", encoding="utf-8")
        merged = note.read_day("2026-09-04")
        check("a second machine's file is merged, not shadowed", len(merged), 3)
        check("and the merge is chronological",
              [n["text"] for n in merged][0], "written somewhere else")
        check("each note carries the machine it came from",
              sorted({n["machine"] for n in merged})[0], "otherbox")

        check_true("the file is named for the day and the machine",
                   note.FILENAME.match(note.path_for(day.date()).name))
        check("one machine writes one file for a day",
              len(list(note.NOTES.glob("2026-09-04.*.md"))), 2)

        # A note is appended, never rewritten, which is what makes last edit
        # wins safe to run over a log.
        mine = note.path_for(day.date())
        before = mine.read_text(encoding="utf-8")
        note.add("a third thing", when=day + timedelta(minutes=5))
        check_true("an existing note is left untouched by a later one",
                   mine.read_text(encoding="utf-8").startswith(before))

        check("a day with nothing in it reads as empty", note.read_day("2026-01-01"), [])
        check("days lists what exists", note.days(), ["2026-09-04"])

        try:
            note.add("   ")
            check("an empty note is refused", "accepted", "refused")
        except ValueError:
            check("an empty note is refused", "refused", "refused")

        hits = [n for n in note.read_day("2026-09-04") if "second" in n["text"]]
        check("a term finds the note carrying it", len(hits), 1)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
