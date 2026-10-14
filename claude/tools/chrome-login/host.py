#!/usr/bin/python3
"""The native-messaging host the Chrome Login extension keeps open.

Chrome starts this process when the extension connects and closes its stdin
when the extension, the profile or the browser goes away. In between it is the
only way in: chrome-login.py writes one JSON request per connection to the unix
socket this opens, the request goes to the extension framed the way Chrome
reads it, and the extension's answer comes back on the same connection.

Each Chrome profile that loads the extension starts its own host, so each one
listens on a socket named for its own pid, and the command line asks every one
that answers. The directory is the owner's alone, which is the whole of the
access check: a process that can reach the socket is already running as the
person whose browser it drives. Nothing passing through is logged or kept.

Chrome launches this with launchd's PATH and nothing of the user's shell, so
it is written for the system interpreter and imports nothing outside the
standard library.
"""
import itertools
import json
import os
import socket
import struct
import sys
import threading

LIMIT = 1 << 20


def socket_dir():
    root = os.environ.get("INTELLIGENCE_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "state")
    return os.path.join(root, "chrome-login", "hosts")


class Relay(object):
    """One extension on stdio, any number of command-line callers on a socket."""

    def __init__(self, inbound, outbound):
        self.inbound, self.outbound = inbound, outbound
        self.write_lock = threading.Lock()
        self.pending = {}
        self.ids = itertools.count(1)

    def send(self, message):
        data = json.dumps(message).encode("utf-8")
        with self.write_lock:
            self.outbound.write(struct.pack("=I", len(data)) + data)
            self.outbound.flush()

    def receive(self):
        head = self.inbound.read(4)
        if len(head) < 4:
            return None
        (size,) = struct.unpack("=I", head)
        return json.loads(self.inbound.read(size).decode("utf-8"))

    def listen_to_extension(self):
        while True:
            try:
                message = self.receive()
            except (OSError, ValueError):
                message = None
            if message is None:
                return
            slot = self.pending.get(message.get("id"))
            if slot is not None:
                slot[1] = message
                slot[0].set()

    def ask(self, request):
        rid = next(self.ids)
        slot = [threading.Event(), None]
        self.pending[rid] = slot
        try:
            self.send(dict(request, id=rid))
            if not slot[0].wait(float(request.get("timeout") or 30)):
                return {"ok": False, "error": "the extension did not answer"}
            answer = dict(slot[1])
            answer.pop("id", None)
            return answer
        finally:
            self.pending.pop(rid, None)

    def serve(self, conn):
        with conn:
            conn.settimeout(90)
            data = b""
            try:
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk or len(data) > LIMIT:
                        return
                    data += chunk
                request = json.loads(data.decode("utf-8"))
                answer = self.ask(request) if isinstance(request, dict) else {
                    "ok": False, "error": "a request is one JSON object"}
                conn.sendall((json.dumps(answer) + "\n").encode("utf-8"))
            except (OSError, ValueError):
                return


def main():
    relay = Relay(sys.stdin.buffer, sys.stdout.buffer)
    directory = socket_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = os.path.join(directory, "%d.sock" % os.getpid())
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(8)

    def accept():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=relay.serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        relay.listen_to_extension()
    finally:
        server.close()
        # The socket is this process's own endpoint, named for its pid; once the
        # extension is gone nothing can answer on it.
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
