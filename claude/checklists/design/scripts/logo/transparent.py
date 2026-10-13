#!/usr/bin/env python3
"""Strips the flat background out of a generated logo and re-encodes it with alpha.

Image models draw a cleaner mark on a white field than on nothing, so the
generation prompt asks for white and the deliverable needs it gone. Doing that by
hand is where the transparency requirement quietly dies, so it happens here:
generate on white, run this, hand over the file with alpha.

The fill starts from the border rather than replacing every white pixel, so white
*inside* the mark - a counter in an O, a knocked-out letterform, a highlight - is
kept. Pixels at the boundary get partial alpha from how close they are to the
background, which is what stops the edge reading as a cut-out.

    transparent.py <in.png> [-o out.png] [--tolerance 12] [--check]

Stdlib only: this has to run in a cloud session with nothing installed.
"""

import argparse
import struct
import sys
import zlib
from collections import deque
from pathlib import Path


def read_png(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("%s is not a PNG" % path)
    pos, idat, palette, trns = 8, bytearray(), None, None
    header = None
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            w, h, depth, colour, comp, filt, interlace = struct.unpack(">IIBBBBB", body)
            header = dict(w=w, h=h, depth=depth, colour=colour, interlace=interlace)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None:
        raise SystemExit("%s has no IHDR" % path)
    if header["depth"] != 8 or header["interlace"] != 0:
        raise SystemExit("%s is %d-bit%s; this handles 8-bit, non-interlaced PNGs"
                         % (path, header["depth"], ", interlaced" if header["interlace"] else ""))
    header["palette"], header["trns"] = palette, trns
    return header, zlib.decompress(bytes(idat))


CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def unfilter(raw, w, h, channels):
    stride = w * channels
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(h):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(len(line)):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            upleft = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + left) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + up) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(line)
        prev = line
    return rows


def to_rgba(header, rows):
    w, h, colour = header["w"], header["h"], header["colour"]
    out = bytearray(w * h * 4)
    palette, trns = header["palette"], header["trns"]
    for y in range(h):
        row = rows[y]
        for x in range(w):
            o = (y * w + x) * 4
            if colour == 6:
                out[o:o + 4] = row[x * 4:x * 4 + 4]
            elif colour == 2:
                out[o:o + 3] = row[x * 3:x * 3 + 3]
                out[o + 3] = 255
            elif colour == 0:
                v = row[x]
                out[o:o + 4] = bytes([v, v, v, 255])
            elif colour == 4:
                v, a = row[x * 2], row[x * 2 + 1]
                out[o:o + 4] = bytes([v, v, v, a])
            elif colour == 3:
                idx = row[x]
                out[o:o + 3] = palette[idx * 3:idx * 3 + 3]
                out[o + 3] = trns[idx] if trns and idx < len(trns) else 255
    return out


def write_png(path, w, h, rgba):
    def chunk(kind, body):
        payload = kind + body
        return struct.pack(">I", len(body)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def background_colour(rgba, w, h):
    """Whatever the border agrees on. Usually white, but a brief may say otherwise."""
    tally = {}
    for x in range(w):
        for y in (0, h - 1):
            o = (y * w + x) * 4
            tally[bytes(rgba[o:o + 3])] = tally.get(bytes(rgba[o:o + 3]), 0) + 1
    for y in range(h):
        for x in (0, w - 1):
            o = (y * w + x) * 4
            tally[bytes(rgba[o:o + 3])] = tally.get(bytes(rgba[o:o + 3]), 0) + 1
    return max(tally.items(), key=lambda kv: kv[1])[0]


def distance(rgba, offset, bg):
    return max(abs(rgba[offset] - bg[0]), abs(rgba[offset + 1] - bg[1]), abs(rgba[offset + 2] - bg[2]))


def strip(rgba, w, h, tolerance, feather):
    """Flood the background in from the edges, then soften what the flood touched."""
    bg = background_colour(rgba, w, h)
    seen = bytearray(w * h)
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            queue.append((x, y))
    cleared = 0
    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        i = y * w + x
        if seen[i]:
            continue
        o = i * 4
        if distance(rgba, o, bg) > tolerance:
            continue
        seen[i] = 1
        rgba[o + 3] = 0
        cleared += 1
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # The pixels one step in from the cleared region are the anti-aliased rim the
    # model drew against white. Scaling their alpha by how far they sit from the
    # background is what keeps the edge soft instead of stair-stepped.
    if feather:
        for y in range(h):
            for x in range(w):
                i = y * w + x
                if seen[i]:
                    continue
                touching = any(0 <= x + dx < w and 0 <= y + dy < h and seen[(y + dy) * w + (x + dx)]
                               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if not touching:
                    continue
                o = i * 4
                d = distance(rgba, o, bg)
                if d < feather:
                    rgba[o + 3] = min(rgba[o + 3], int(255 * d / feather))
    return bg, cleared


def corners_clear(rgba, w, h):
    return [rgba[3], rgba[(w - 1) * 4 + 3], rgba[(h - 1) * w * 4 + 3], rgba[(h * w - 1) * 4 + 3]]


def main():
    ap = argparse.ArgumentParser(description="Give a generated logo a transparent background")
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="defaults to <name>-transparent.png")
    ap.add_argument("--tolerance", type=int, default=12, help="how far a pixel may drift from the background colour and still count as background")
    ap.add_argument("--feather", type=int, default=40, help="rim softening width; 0 turns it off")
    ap.add_argument("--check", action="store_true", help="report transparency without writing anything")
    args = ap.parse_args()

    header, raw = read_png(args.source)
    w, h = header["w"], header["h"]
    rows = unfilter(raw, w, h, CHANNELS[header["colour"]])
    rgba = to_rgba(header, rows)

    if args.check:
        alphas = corners_clear(rgba, w, h)
        if all(a == 0 for a in alphas):
            print("%s: transparent at all four corners" % args.source.name)
            return 0
        print("%s: corners are opaque (alpha %s) - background still present" % (args.source.name, alphas))
        return 1

    bg, cleared = strip(rgba, w, h, args.tolerance, args.feather)
    out = args.output or args.source.with_name(args.source.stem + "-transparent.png")
    write_png(out, w, h, rgba)
    pct = 100.0 * cleared / (w * h)
    print("%s -> %s" % (args.source.name, out.name))
    print("background rgb%s, %d px cleared (%.1f%% of the image)" % (tuple(bg), cleared, pct))
    if pct < 1:
        print("Almost nothing was cleared - raise --tolerance, or the mark may already fill the frame.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
