#!/usr/bin/env python3
"""The conversations held outside Claude Code, read for reaction.py.

Two places the user talks to the assistant leave no Claude Code transcript:

  claude.ai chats, which the Claude desktop app caches in its IndexedDB - a
  LevelDB of V8-serialized values, the larger ones snappy-compressed into blob
  files. Read here with no dependencies: the log and table files, the blobs,
  snappy, and enough of the V8 format to walk a conversation tree.

  the voice assistant's spoken conversation, the host's speech log - every
  line heard from the user and every line said back, one JSON row each - read
  on the host itself or over ssh.

Each yields the same thing, an exchange: the reply the assistant gave, the
user's next message, and the user's messages before it. What a message says
about the reply is reaction.py's to judge.

    elsewhere.py claude.ai [--days 14]    the exchanges in the claude.ai cache
    elsewhere.py voice [--days 14]        the exchanges in the spoken log
"""
import glob
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / "Library" / "Application Support" / "Claude" / "IndexedDB"


# --- snappy ---------------------------------------------------------------------

def varint(buf, i):
    shift = value = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def unsnappy(buf):
    size, i = varint(buf, 0)
    out = bytearray()
    while i < len(buf):
        tag = buf[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                extra = length - 59
                length = int.from_bytes(buf[i:i + extra], "little")
                i += extra
            length += 1
            out += buf[i:i + length]
            i += length
            continue
        if kind == 1:
            length = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | buf[i]
            i += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            offset = int.from_bytes(buf[i:i + 2], "little")
            i += 2
        else:
            length = (tag >> 2) + 1
            offset = int.from_bytes(buf[i:i + 4], "little")
            i += 4
        if offset <= 0 or offset > len(out):
            raise ValueError("bad snappy offset")
        start = len(out) - offset
        for k in range(length):
            out.append(out[start + k])
    if len(out) != size:
        raise ValueError("snappy length mismatch")
    return bytes(out)


# --- LevelDB --------------------------------------------------------------------

def log_values(path):
    """Every value written in a LevelDB log file."""
    data = Path(path).read_bytes()
    records, pending, block = [], b"", 32768
    for base in range(0, len(data), block):
        chunk, i = data[base:base + block], 0
        while i + 7 <= len(chunk):
            length = struct.unpack_from("<H", chunk, i + 4)[0]
            kind = chunk[i + 6]
            body = chunk[i + 7:i + 7 + length]
            i += 7 + length
            if kind == 0 and length == 0:
                break
            if kind == 1:
                records.append(body)
            elif kind == 2:
                pending = body
            elif kind == 3:
                pending += body
            elif kind == 4:
                records.append(pending + body)
                pending = b""
    for batch in records:
        i, count = 12, struct.unpack_from("<I", batch, 8)[0]
        for _ in range(count):
            if i >= len(batch):
                break
            tag = batch[i]
            i += 1
            size, i = varint(batch, i)
            i += size
            if tag == 1:
                size, i = varint(batch, i)
                yield batch[i:i + size]
                i += size


def block_values(block):
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end = len(block) - 4 - 4 * restarts
    i = 0
    while i < end:
        _, i = varint(block, i)
        unshared, i = varint(block, i)
        size, i = varint(block, i)
        i += unshared
        yield block[i:i + size]
        i += size


def table_values(path):
    """Every value in a LevelDB table file."""
    data = Path(path).read_bytes()
    footer = data[-48:]
    _, j = varint(footer, 0)
    _, j = varint(footer, j)
    offset, j = varint(footer, j)
    size, j = varint(footer, j)

    def read(off, n):
        raw, kind = data[off:off + n], data[off + n]
        return unsnappy(raw) if kind == 1 else raw

    for handle in block_values(read(offset, size)):
        off, k = varint(handle, 0)
        n, _ = varint(handle, k)
        try:
            yield from block_values(read(off, n))
        except (ValueError, IndexError, struct.error):
            continue


# --- V8 values ------------------------------------------------------------------

class V8:
    """Just enough of the V8 serializer to walk plain objects, arrays, strings and numbers."""

    def __init__(self, buf, i):
        self.buf, self.i = buf, i

    def varint(self):
        value, self.i = varint(self.buf, self.i)
        return value

    def value(self):
        buf = self.buf
        while buf[self.i] == 0:
            self.i += 1
        tag = chr(buf[self.i])
        self.i += 1
        if tag == '"':
            n = self.varint()
            s = buf[self.i:self.i + n].decode("latin-1")
            self.i += n
            return s
        if tag == "c":
            n = self.varint()
            s = buf[self.i:self.i + n].decode("utf-16-le", "replace")
            self.i += n
            return s
        if tag == "S":
            n = self.varint()
            s = buf[self.i:self.i + n].decode("utf-8", "replace")
            self.i += n
            return s
        if tag == "I":
            n = self.varint()
            return (n >> 1) ^ -(n & 1)
        if tag == "U":
            return self.varint()
        if tag in "ND":
            value = struct.unpack_from("<d", buf, self.i)[0]
            self.i += 8
            return value
        if tag == "T":
            return True
        if tag == "F":
            return False
        if tag in "_0-":
            return None
        if tag == "^":
            self.varint()
            return None
        if tag == "o":
            out = {}
            while chr(buf[self.i]) != "{":
                key = self.value()
                out[str(key)] = self.value()
            self.i += 1
            self.varint()
            return out
        if tag == "A":
            n = self.varint()
            out = [self.value() for _ in range(n)]
            while chr(buf[self.i]) != "$":
                self.value()
                self.value()
            self.i += 1
            self.varint()
            self.varint()
            return out
        if tag == "a":
            n = self.varint()
            out = [None] * min(n, 100000)
            while chr(buf[self.i]) != "@":
                key = self.value()
                value = self.value()
                if isinstance(key, int) and 0 <= key < len(out):
                    out[key] = value
            self.i += 1
            self.varint()
            self.varint()
            return out
        if tag == "\xff":
            self.varint()
            return self.value()
        raise ValueError("unhandled V8 tag %r" % tag)


TREE = b'"\x04treeo'


def trees(blob):
    """Every conversation tree serialized in this value."""
    at = blob.find(TREE)
    while at >= 0:
        try:
            tree = V8(blob, at + len(TREE) - 1).value()
            if isinstance(tree, dict) and tree.get("conversationUuid"):
                yield tree
        except (ValueError, IndexError, struct.error, UnicodeDecodeError):
            pass
        at = blob.find(TREE, at + 1)


def unwrap(value):
    """A cached value as raw bytes: snappy-wrapped ones decompressed."""
    mark = value.find(b"\xff\x11\x02")
    if 0 <= mark < 8:
        try:
            return unsnappy(value[mark + 3:])
        except (ValueError, IndexError):
            return value
    return value


def cached_values(root=None):
    root = Path(root or CACHE)
    for db in glob.glob(str(root / "*.indexeddb.leveldb")):
        for path in sorted(glob.glob(db + "/*.log")):
            try:
                yield from (unwrap(v) for v in log_values(path))
            except (OSError, IndexError, struct.error):
                continue
        for path in sorted(glob.glob(db + "/*.ldb")):
            try:
                yield from (unwrap(v) for v in table_values(path))
            except (OSError, IndexError, struct.error, ValueError):
                continue
    for path in glob.glob(str(root / "*.indexeddb.blob" / "**" / "*"), recursive=True):
        if os.path.isfile(path):
            try:
                yield unwrap(Path(path).read_bytes())
            except OSError:
                continue


def epoch(stamp):
    try:
        return time.mktime(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, OverflowError):
        return 0.0


def words_of(message):
    parts = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
    if not parts and message.get("text"):
        parts.append(str(message["text"]))
    text = "\n".join(parts)
    try:
        # One-byte V8 strings are Latin-1 by definition, but the app writes UTF-8
        # text into them when every byte fits; put it back where it does.
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def claude_ai(days=14, root=None):
    """The exchanges in the claude.ai cache from the last `days` days, oldest first."""
    messages = {}
    for value in cached_values(root):
        for tree in trees(value):
            convo = tree.get("conversationUuid")
            for m in tree.get("messages") or []:
                if isinstance(m, dict) and m.get("uuid") and m.get("sender") in ("human", "assistant"):
                    m = dict(m, conversation=convo)
                    messages[m["uuid"]] = m
    by_convo = {}
    for m in messages.values():
        by_convo.setdefault(m["conversation"], []).append(m)
    cutoff = time.time() - 86400 * days
    out = []
    for convo, found in by_convo.items():
        found.sort(key=lambda m: (epoch(m.get("created_at")), m.get("index") or 0))
        said, reply = [], ""
        for m in found:
            text = words_of(m)
            if m["sender"] == "assistant":
                reply = text
                continue
            at = epoch(m.get("created_at"))
            if reply and text.strip() and at >= cutoff:
                out.append({"source": "claude.ai", "key": "claude.ai:%s" % m["uuid"], "at": at,
                            "session": convo, "reply": reply, "said": text, "earlier": said[-3:]})
            if text.strip():
                said.append(text)
            reply = ""
    out.sort(key=lambda x: x["at"])
    return out


# --- the spoken conversation ----------------------------------------------------

def profile_value(key, default=""):
    sys.path.insert(0, str(ROOT))
    import ratchet
    return ratchet.profile_value(key, default) or default


def voice_rows(log, since):
    """Rows of the spoken log newer than `since`: read here when this is the host, else over ssh."""
    local = Path(log)
    if local.is_file():
        rows = []
        with local.open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if float(row.get("at") or 0) >= since:
                    rows.append(row)
        return rows
    host = profile_value("host.ssh")
    if not host:
        return []
    script = ("import json,sys\nfor l in open(%r):\n try:\n  r=json.loads(l)\n except ValueError:\n  continue\n"
              " if float(r.get('at') or 0)>=%f and r.get('kind') in ('heard','said'):\n  print(json.dumps(r))\n") % (str(log), since)
    try:
        done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host, "python3", "-"],
                              input=script, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in done.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def spoken(row):
    sentences = row.get("sentences")
    if isinstance(sentences, list):
        return " ".join(str(s.get("text") if isinstance(s, dict) else s) for s in sentences).strip()
    return str(row.get("intended") or row.get("text") or "").strip()


def voice(days=14, log=None):
    """The exchanges in the spoken log from the last `days` days, oldest first."""
    sys.path.insert(0, str(ROOT))
    import reaction
    log = log or os.path.join(profile_value("host.home", "~"), reaction.rules().get("voice_log") or "")
    rows = sorted(voice_rows(log, time.time() - 86400 * days), key=lambda r: float(r.get("at") or 0))
    out, said, reply, reply_at = [], [], "", 0.0
    for row in rows:
        if row.get("kind") == "said":
            text = spoken(row)
            if text:
                reply, reply_at = text, float(row.get("at") or 0)
            continue
        if row.get("kind") != "heard" or row.get("discarded") or not str(row.get("text") or "").strip():
            continue
        at, text = float(row.get("at") or 0), str(row["text"]).strip()
        if reply and at - reply_at < 900:
            out.append({"source": "voice", "key": "voice:%s" % (row.get("qid") or at), "at": at,
                        "session": "voice", "reply": reply, "said": text, "earlier": said[-3:]})
        said.append(text)
        reply = ""
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("place", choices=["claude.ai", "voice"])
    parser.add_argument("--days", type=float, default=14)
    args = parser.parse_args()
    found = claude_ai(args.days) if args.place == "claude.ai" else voice(args.days)
    for x in found:
        print(json.dumps({"at": time.strftime("%b %-d %-I:%M %p", time.localtime(x["at"])),
                          "reply": x["reply"][:100], "said": x["said"][:120]}))
    print("%d exchanges" % len(found), file=sys.stderr)
