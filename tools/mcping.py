"""Minecraft server list ping (SLP) from the command line.

Usage:
    python tools/mcping.py play.example.com
    python tools/mcping.py 192.168.1.10 25565
"""
import json
import socket
import struct
import sys


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes((b | 0x80,))
        else:
            out += bytes((b,))
            return out


def read_varint(sock):
    n = 0
    shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise EOFError
        b = b[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7


def mc_status(host, port=25565, timeout=4):
    s = socket.create_connection((host, port), timeout)
    s.settimeout(timeout)
    hb = host.encode()
    body = (b"\x00" + varint(47) + varint(len(hb)) + hb
            + struct.pack(">H", port) + varint(1))
    s.sendall(varint(len(body)) + body)
    s.sendall(b"\x01\x00")
    ln = read_varint(s)
    data = b""
    while len(data) < ln:
        chunk = s.recv(ln - len(data))
        if not chunk:
            break
        data += chunk
    s.close()
    i = 0
    while data[i] & 0x80:
        i += 1
    i += 1
    slen = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        slen |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    return json.loads(data[i:i + slen].decode("utf-8", "replace"))


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    host = argv[0]
    port = int(argv[1]) if len(argv) > 1 else 25565
    try:
        j = mc_status(host, port)
    except Exception as e:
        print("MC %s:%d ERR %r" % (host, port, e))
        return 2
    print("MC %s:%d ->" % (host, port))
    print(json.dumps(j, indent=2, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
