#!/usr/bin/env python3
"""Hook entry for ratchet. Wired to Stop and SubagentStop in settings.json.

A separate file from the engine on purpose: settings.json names this path on
every machine the mirror reaches, and the engine underneath is free to move.
Nothing here can fail loudly - a stop-check that crashes would end every turn in
an error, so anything unexpected lets the stop through.

  hook.py stop        Stop
  hook.py subagent    SubagentStop
  hook.py say         PreToolUse on Bash (a Sunday announcement)
  hook.py prompt      UserPromptSubmit (the reply shape, before the turn writes)
  hook.py display     MessageDisplay (text out of shape is kept off the screen)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import ratchet
except Exception:
    sys.exit(0)

if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) > 1 else "stop"
    try:
        if verb == "say":
            sys.exit(ratchet.pre_say() or 0)
        if verb == "prompt":
            sys.exit(ratchet.prompt_context() or 0)
        if verb == "display":
            sys.exit(ratchet.display() or 0)
        sys.exit(ratchet.hook(verb) or 0)
    except Exception:
        sys.exit(0)
