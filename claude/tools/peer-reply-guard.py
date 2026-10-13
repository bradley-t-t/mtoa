#!/usr/bin/env python3
"""Refuse a session-to-session message whose whole content is that no message
was needed.

A hold is honoured by holding. The session that sent it waits on nothing and
reads git, `gh` and `list_sessions` itself, so "nothing in flight here",
"develop moved, rebase" and "leave my worktree in place for your sweep" cost it
a turn to learn what it already had.

What still passes: a hold announcement, a hold lift, a stop notice, a message
asking a peer to do something, and the answer to one. The test is what is left
once the hold mechanics are struck out sentence by sentence - the
acknowledgement, the hold's own terms said back, this session's clearance, its
self-description, and the git state the recipient reads itself. A message with
nothing behind those is refused; a real finding wrapped in an acknowledgement
leaves a sentence standing and passes.

Wired as PreToolUse. `PEER_REPLY_GUARD_OFF=1` stands it down for one run.
"""
import json
import os
import re
import sys

TOOLS = {"mcp__ccd_session_mgmt__send_message", "SendMessage", "send_message"}

# A hold announcement, its lift, or a stop notice. Always allowed.
CARRIES = re.compile(
    r"^\s*(running /|done with /|failed |failing |stopped at )"
    r"|stop sending me (holds|messages)",
    re.I,
)

# The opening clause acknowledges a hold rather than carrying anything.
ACK_OPENER = re.compile(
    r"^\s*[\"'*_]*"
    r"(acknowledg\w*|holding|held|honou?red|noted|understood|will hold|"
    r"confirm(ed|ing)?( again)?|re-?confirm\w*|"
    r"clear (from|here|to land|on my side)|nothing to hold|"
    r"nothing (in flight|pending|of mine|queued|to push)|"
    r"no (push|action|reply|objection|conflict|collision))\b",
    re.I,
)

# Says only that this session is not in the sender's way.
NOT_IN_YOUR_WAY = re.compile(
    r"nothing (in flight|pending|of mine|queued|pushing|further|for your)"
    r"|nothing from (here|this session|my side)"
    r"|no (push|commits|pull request|pr)\b[^.]{0,40}"
    r"(coming|planned|pending|queued|to make|from (here|me))"
    r"|(has|have|with) no push\b"
    r"|is clean\b|clean (tree|worktree)"
    r"|(go ahead|the repo is (yours|free|clear)|develop is clear)"
    r"|nothing (to|for) (push|land|your)"
    r"|will (not|never) (touch|reach|hit) the remote"
    r"|no action (needed|required)|not applicable|does not (apply|affect)"
    r"|no (commits?|pushes|branches|prs?|pull requests?)[^.]{0,60}"
    r"(at any point|from this session|of my own)"
    r"|nothing written into the tree|no branch of my own"
    r"|nothing queued behind you|no collision with me"
    r"|never (pushes|opens|deploys|merges)",
    re.I,
)

# A sha, a pull request, a version, a path - something the message actually carries.
A_FACT = re.compile(
    r"\b[0-9a-f]{7,40}\b|#\d+|\b20\d\d\.\d+\.\d+\b"
    r"|\.(jsx?|tsx?|py|md|json|css|html)\b|https?://|\b\w+\.(com|net|org|dev)\b")

# The hold's own terms, said back to the session that sent them.
ECHOES_THE_HOLD = re.compile(
    r"(will not|won'?t|do not|shall not) (push|land|commit|merge|open)"
    r"|until you (report|say|are) (done|finished|back)"
    r"|no (pushes|prs|pull requests)[^.]{0,60}until you"
    r"|ping me (when|either|once)"
    r"|(holding|hold|held)\b[^.]{0,40}(until you|through your|for your) "
    r"|(the )?(path|repo|remote|develop) is (clear|yours|free|open)"
    r"|take the time you need"
    r"|the hold (stands|holds|remains)"
    r"|until (you|your) [a-z/ ]{0,30}(report|finish|land|is done)"
    r"|my branch (just )?sits until",
    re.I,
)

# The session describing where it is standing, which the recipient reads from
# `git worktree list` and `list_sessions`.
SELF_DESCRIPTION = re.compile(
    r"^\s*(i('m| am| have| ha ?ve)?\b|i'?ve\b|my\b|this session\b|this is a\b|"
    r"everything (i|i'?ve|my)\b|the (branch|worktree|tree)\b)"
    r"[^.]{0,140}"
    r"\b(branch|worktree|checkout|tree|repo|session|develop|main)\b",
    re.I,
)

# Facts the recipient reads from a source of truth on its own.
ALREADY_READ = re.compile(
    r"rebase (onto|before|on|your branch|it|first|that)\b"
    r"|whoever lands (second|last) rebases|so rebase\b|expect (a|the) rebase"
    r"|\b(origin/)?(develop|main)\b[^.]{0,60}"
    r"\b(moved|is at|is now|carries|sits at|has no|are level|is clear|unreleased)\b"
    r"|\b(develop|main) [0-9a-f]{7}"
    r"|your branch (is|should be|was) (cut|at|behind)"
    r"|no (prs?|pull requests?) (are )?open"
    r"|\b20\d\d\.\d+\.\d+ (is|was) (live|yours|out)"
    r"|list_sessions will show"
    r"|leave (that|this|it) (directory|worktree|in place)"
    r"|live-(cwd|session-cwd)|for your sweep|your sweep (will|needs|removes)"
    r"|i am live in\b|this session is live in\b"
    r"|(your|the) (/merge|merge|release|run) is unobstructed",
    re.I,
)

# A sentence that is only hold mechanics or this session's own clearance.
def _is_procedural(sentence):
    return bool(
        ACK_OPENER.match(sentence)
        or ECHOES_THE_HOLD.search(sentence)
        or NOT_IN_YOUR_WAY.search(sentence)
        or ALREADY_READ.search(sentence)
        or (SELF_DESCRIPTION.match(sentence) and not A_FACT.search(
            SELF_DESCRIPTION.sub("", sentence, count=1)))
    )


# A list marker, a heading stub, a bare verdict - a splitter artifact, not a
# sentence anyone could act on.
FRAGMENT = re.compile(
    r"^([0-9]+[.)]|[-*\u2022]|clean|agreed|correct|right|same here|ditto|"
    r"(one|two|three|four|a few|several) (thing|note|point)s?[^.]{0,40}:)\s*$",
    re.I,
)


def _residue(body):
    """What the message says once the hold mechanics are struck out.

    A sentence has to carry something to count. Splitting on punctuation leaves
    list markers and stubs behind, and a message is not carrying a finding
    because it said "2." somewhere.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", body) if p.strip()]
    kept = []
    for part in parts:
        if _is_procedural(part) or FRAGMENT.match(part):
            continue
        if len(part.split()) < 5 and not A_FACT.search(part):
            continue
        kept.append(part)
    return kept

REFUSAL = (
    "Blocked: this reads as a reply to a hold.\n\n"
    "A hold is honoured by holding, not by answering. The session that sent it "
    "waits on nothing and reads git, `gh` and `list_sessions` itself, so "
    "\"nothing in flight here\", \"develop moved, rebase\" and \"leave my "
    "worktree in place for your sweep\" each cost it a turn to learn what it "
    "already had.\n\n"
    "The message about to be sent:\n\n{quoted}\n\n"
    "Send nothing and carry on. The exceptions are a stop notice "
    "(`I am not in <slug>. Stop sending me holds for it.`), a message asking a "
    "peer to do something, and the answer to one - and that answer is doing it. "
    "If this is one of those, say which in the message itself rather than "
    "opening with an acknowledgement."
)


def verdict(text):
    """Return a refusal string, or None to allow."""
    body = (text or "").strip()
    if not body:
        return None
    if CARRIES.search(body):
        return None

    # It has to open as hold mechanics to be a reply to a hold at all.
    first = body.split("\n\n", 1)[0]
    if not (ACK_OPENER.match(first)
            or ECHOES_THE_HOLD.search(first)
            or NOT_IN_YOUR_WAY.search(first)):
        return None

    # And it has to say nothing once those mechanics are struck out. One
    # sentence of real finding left standing is enough to send it.
    if _residue(body):
        return None

    return REFUSAL.format(quoted="> " + body[:600].replace("\n", "\n> "))


def selftest():
    """Every case in the corpus, which is real traffic plus hand-written edges."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "peer-reply-corpus.json")
    corpus = json.load(open(path))
    hard, drift = [], []
    for key, want_block, bucket in (
        ("hand_must_allow", False, hard), ("hand_must_block", True, hard),
        ("must_allow", False, drift), ("must_block", True, drift),
    ):
        for text in corpus.get(key, []):
            got_block = verdict(text) is not None
            if got_block != want_block:
                bucket.append((key, text))

    for key, text in hard:
        print(f"FAIL {key}: {text[:140]}")
    for key, text in drift[:12]:
        print(f"drift {key}: {text[:120]}")
    if len(drift) > 12:
        print(f"drift ... and {len(drift) - 12} more")

    n_hand = len(corpus.get("hand_must_allow", [])) + len(corpus.get("hand_must_block", []))
    n_reg = len(corpus.get("must_allow", [])) + len(corpus.get("must_block", []))
    print(f"hand-labelled {n_hand - len(hard)}/{n_hand} pass; "
          f"regression baseline {n_reg - len(drift)}/{n_reg} unchanged")
    if drift and not hard:
        print("Drift is not a failure by itself - read the lines above and, if the "
              "new verdicts are right, rebuild the baseline.")
    return 1 if hard else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()
    if len(sys.argv) > 2 and sys.argv[1] == "check":
        print(verdict(sys.argv[2]) or "allowed")
        return 0
    if os.environ.get("PEER_REPLY_GUARD_OFF") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    name = payload.get("tool_name") or ""
    if name not in TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    text = tool_input.get("message") or tool_input.get("prompt") or ""
    reason = verdict(text)
    if reason is None:
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
