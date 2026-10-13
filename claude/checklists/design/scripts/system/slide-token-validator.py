#!/usr/bin/env python3
"""Validates slide HTML against the design tokens.

html-token-validator.py checks every kind of HTML asset; this is that check
pointed at slides, so a slide deck can be validated without naming a type.

    python html-token-validator.py --type slides         # the same check
    python html-token-validator.py --type infographics
    python html-token-validator.py                       # every HTML asset
"""

import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
UNIFIED_VALIDATOR = SCRIPT_DIR / 'html-token-validator.py'


def main():
    """Run the HTML validator over the slides, passing through any arguments."""
    args = sys.argv[1:]

    # Named files decide their own type, so --type is only supplied when the
    # call carries nothing but flags.
    if not args or all(arg.startswith('-') for arg in args):
        cmd = [sys.executable, str(UNIFIED_VALIDATOR), '--type', 'slides'] + args
    else:
        cmd = [sys.executable, str(UNIFIED_VALIDATOR)] + args

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
