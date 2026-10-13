#!/usr/bin/env bash
# Move this directory onto the host, and say when the host has moved without it.
#
#   deploy.sh check          what differs between this checkout and the Pi
#   deploy.sh push [file]    copy the tracked files across and restart what changed
#   deploy.sh diff <file>    the actual diff for one file, checkout against the host
#   deploy.sh units [push]   the systemd units, rendered for the host, against its own
#
# The host runs from ~/<service>/, never from a checkout, and for a
# long time nothing carried one to the other. The copies drifted, and drift here
# is silent in both directions: the repository looks deployable and the Pi looks
# healthy, right up until somebody copies a tracked file over a deployed one and
# takes a service down with work the tree never had. That is not hypothetical -
# the collector's status feed ran 160 lines ahead of the tree for eleven days,
# and a fix in that window had to be applied to the deployed file by hand
# because the obvious deployment step would have stopped the status board.
#
# So `check` is the verb that matters and `push` is the easy half. Run `check`
# before trusting the tree and after any change on the Pi; it exits non-zero the
# moment the two disagree, which is the thing that used to be invisible.
#
# Only tracked source travels. config.json, state.json and the ledgers hold
# secrets or accumulated state that belongs to the machine, and nothing here
# reads or writes them.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# The host is the profile's (host.ssh); SUNDAY_HOST names another for one run.
profile() {
  local reader
  for reader in "$HERE/services/lib/mtoa_config.py" "$HOME/.claude/tools/mtoa_config.py"; do
    [ -f "$reader" ] && { python3 "$reader" get "$@"; return; }
  done
}
HOST=${SUNDAY_HOST:-$(profile host.ssh)}
if [ -z "$HOST" ]; then
  echo "deploy: no host - set host.ssh in the profile or SUNDAY_HOST" >&2
  exit 64
fi

# Every file this repository is the source of, and where it lives on the host,
# is a row of bin/deploy.map; see the head of that file.
MAP=$(sed -e '/^[[:space:]]*#/d' "$HERE/bin/deploy.map")

rows() { echo "$MAP" | sed '/^[[:space:]]*$/d'; }

# The Pi's copy of every mapped file, hashed in one round trip rather than one
# ssh per file.
remote_hashes() {
  local paths=""
  while IFS='|' read -r src dest _; do
    [ -n "$src" ] || continue
    case "$dest" in */) paths="$paths $dest$(basename "$src")" ;; *) paths="$paths $dest" ;; esac
  done < <(rows)
  # shellcheck disable=SC2086
  ssh -o ConnectTimeout=20 "$HOST" "md5sum $paths 2>/dev/null" 2>/dev/null
}

remote_path() {
  case "$2" in */) echo "$2$(basename "$1")" ;; *) echo "$2" ;; esac
}

cmd_check() {
  local hashes drift=0 missing=0
  hashes=$(remote_hashes)
  while IFS='|' read -r src dest unit; do
    [ -n "$src" ] || continue
    local rp mine theirs
    rp=$(remote_path "$src" "$dest")
    mine=$(md5 -q "$HERE/$src" 2>/dev/null || md5sum "$HERE/$src" 2>/dev/null | cut -d' ' -f1)
    theirs=$(echo "$hashes" | awk -v p="$rp" '$2 == p {print $1}')
    if [ -z "$mine" ]; then
      printf "  %-56s not in this checkout\n" "$src"; drift=1
    elif [ -z "$theirs" ]; then
      printf "  %-56s not on the Pi\n" "$src"; missing=1
    elif [ "$mine" != "$theirs" ]; then
      printf "  %-56s DIFFERS\n" "$src"; drift=1
    fi
  done < <(rows)
  if [ "$drift" = 0 ] && [ "$missing" = 0 ]; then
    echo "deploy: the Pi matches this checkout"
    return 0
  fi
  [ "$missing" = 1 ] && echo "deploy: some files are not on the Pi yet - deploy.sh push puts them there"
  [ "$drift" = 1 ] && echo "deploy: the Pi and this checkout disagree - deploy.sh diff <file> reads one of them"
  return 1
}

cmd_diff() {
  local want="$1" found=0
  while IFS='|' read -r src dest unit; do
    [ -n "$src" ] || continue
    case "$src" in *"$want"*) ;; *) continue ;; esac
    found=1
    echo "=== $src"
    ssh -o ConnectTimeout=20 "$HOST" "cat '$(remote_path "$src" "$dest")'" 2>/dev/null \
      | diff -u - "$HERE/$src" | sed 's/^--- -/--- the Pi/;s|^+++ .*|+++ this checkout|'
  done < <(rows)
  [ "$found" = 1 ] || { echo "deploy: nothing in the map matches '$want'"; return 1; }
}

cmd_push() {
  local want="${1:-}" units="" pushed=0
  while IFS='|' read -r src dest unit; do
    [ -n "$src" ] || continue
    if [ -n "$want" ]; then case "$src" in *"$want"*) ;; *) continue ;; esac; fi
    [ -f "$HERE/$src" ] || { echo "  $src is not in this checkout, skipped"; continue; }
    if scp -q "$HERE/$src" "$HOST:$dest"; then
      printf "  %-56s -> %s\n" "$src" "$dest"
      pushed=$((pushed + 1))
      [ -n "$unit" ] && case " $units " in *" $unit "*) ;; *) units="$units $unit" ;; esac
    else
      echo "  $src FAILED to copy"; return 1
    fi
  done < <(rows)
  echo "deploy: $pushed file(s) copied"
  for unit in $units; do
    echo "  restarting $unit"
    ssh -o ConnectTimeout=20 "$HOST" "sudo systemctl restart $unit" || return 1
  done
  [ -n "$units" ] && sleep 8
  for unit in $units; do
    printf "  %s: %s\n" "$unit" "$(ssh -o ConnectTimeout=20 "$HOST" "systemctl is-active $unit")"
  done
  cmd_check
}

# The systemd units are templates: @USER@ and @HOME@ stand for the account the
# services run as, filled from the host itself, so one tree installs on any
# machine. `units` compares what they render to against /etc/systemd/system, and
# `units push` installs the ones that differ and restarts nothing but systemd.
render_units() {
  local account home
  account=$(ssh -o ConnectTimeout=20 "$HOST" 'id -un') || return 1
  home=$(ssh -o ConnectTimeout=20 "$HOST" 'echo "$HOME"') || return 1
  RENDERED=$(mktemp -d)
  for unit in "$HERE"/services/*/*.service "$HERE"/services/*/*.timer; do
    [ -f "$unit" ] || continue
    sed -e "s|@USER@|$account|g" -e "s|@HOME@|$home|g" "$unit" > "$RENDERED/$(basename "$unit")"
  done
}

cmd_units() {
  local install="${1:-}" differ=0 name theirs mine
  render_units || { echo "deploy: the host did not answer"; return 1; }
  for unit in "$RENDERED"/*; do
    name=$(basename "$unit")
    mine=$(md5 -q "$unit" 2>/dev/null || md5sum "$unit" | cut -d' ' -f1)
    theirs=$(ssh -o ConnectTimeout=20 "$HOST" "md5sum /etc/systemd/system/$name 2>/dev/null" | cut -d' ' -f1)
    [ "$mine" = "$theirs" ] && continue
    differ=1
    printf "  %-40s %s\n" "$name" "${theirs:+DIFFERS}${theirs:-not installed}"
    if [ "$install" = push ]; then
      scp -q "$unit" "$HOST:/tmp/$name" && ssh "$HOST" "sudo install -m644 /tmp/$name /etc/systemd/system/$name && rm -f /tmp/$name" \
        || { echo "  $name FAILED to install"; rm -rf "$RENDERED"; return 1; }
    fi
  done
  if [ "$install" = push ] && [ "$differ" = 1 ]; then
    ssh "$HOST" 'sudo systemctl daemon-reload' && echo "deploy: units installed, systemd reloaded"
  fi
  rm -rf "$RENDERED"
  [ "$differ" = 0 ] && echo "deploy: every unit on the host matches what this checkout renders"
  [ "$install" = push ] || return "$differ"
}

case "${1:-check}" in
  check) cmd_check ;;
  push)  cmd_push "${2:-}" ;;
  units) cmd_units "${2:-}" ;;
  diff)  [ -n "${2:-}" ] || { echo "deploy.sh diff <file>"; exit 1; }; cmd_diff "$2" ;;
  *) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
