#!/usr/bin/env bash
# Install M.T.O.A.: clone it to ~/.mtoa (or pull it if it is already there), then
# install the Claude setup into ~/.claude and start a profile.
#
#   curl -fsSL https://raw.githubusercontent.com/bradley-t-t/mtoa/main/install.sh | bash
#   MTOA_DIR=~/src/mtoa bash install.sh --force
#
# Arguments are passed to `mtoa install`. Nothing in ~/.claude you have edited is
# replaced without --force, and --force keeps what it replaces.
set -euo pipefail

REPO="${MTOA_REPO:-https://github.com/bradley-t-t/mtoa.git}"
DIR="${MTOA_DIR:-$HOME/.mtoa}"

for need in git python3; do
  if ! command -v "$need" >/dev/null 2>&1; then
    echo "mtoa: $need is not installed; install it and run this again" >&2
    exit 1
  fi
done

if [ -d "$DIR/.git" ]; then
  echo "updating $DIR"
  git -C "$DIR" pull --ff-only --quiet
elif [ -e "$DIR" ]; then
  echo "mtoa: $DIR exists and is not a checkout; set MTOA_DIR to somewhere else" >&2
  exit 1
else
  echo "cloning into $DIR"
  git clone --quiet --depth 1 "$REPO" "$DIR"
fi

python3 "$DIR/bin/mtoa" install "$@"
echo
echo "next: python3 $DIR/bin/mtoa doctor"
