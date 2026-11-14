#!/usr/bin/env python3
"""Network check for the fusion server. Standard library only, so it runs on the Macs, Windows and the UNO Q.

Usage:  python tools/net_check.py [server_url]
        (default: SERVER_URL env var, else config/server.json)

Checks, in order: DNS -> TCP connect -> HTTPS GET /world -> WebSocket upgrade on /tracks.
Also prints this machine's LAN IPs. Exit code 0 only if every check passes.
Quest / iPhone: they can't run this, so open <server_url>/world in the Quest browser / iPhone Safari
instead. Seeing the JSON means DNS, TCP and HTTPS work. For iPhones, also see tools/iphone_cert_help.sh.
"""
import base64
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 4.0
results = []


def report(name, ok, detail, warn=False):
    tag = "WARN" if (ok and warn) else ("PASS" if ok else "FAIL")
    results.append(ok)
    print(f"  [{tag}] {name:10s} {detail}")


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet sent; picks the outbound interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    for cmd in (["ifconfig"], ["ip", "-4", "-o", "addr"], ["ipconfig"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        ips.update(re.findall(r"(?:inet (?:addr:)?|IPv4 Address[ .]*: )(\d+\.\d+\.\d+\.\d+)", out))
        if out:
            break
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def default_url():
    sys.path.insert(0, str(ROOT))
    from config.server_url import server_url
    return server_url()


def ssl_ctx(verify):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_http(base, verify):
    req = urllib.request.Request(base + "/world", headers={"User-Agent": "net_check"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_ctx(verify) if base.startswith("https") else None) as r:
        body = r.read()
        ms = (time.time() - t0) * 1000
    try:
        keys = ", ".join(sorted(json.loads(body))) or "(empty)"
    except ValueError:
        return f"HTTP {r.status} in {ms:.0f} ms, but the body is not JSON", False
    return f"HTTP {r.status} in {ms:.0f} ms, keys: {keys}", True


def check_ws(host, port, secure, verify, path="/tracks"):
    sock = socket.create_connection((host, port), timeout=TIMEOUT)
    if secure:
        sock = ssl_ctx(verify).wrap_socket(sock, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 101 " not in status + " ":
        sock.close()
        return f"no upgrade: {status or 'connection closed'}", False
    sock.settimeout(2.0)
    try:
        got = len(rest) or len(sock.recv(65536))
        extra = f", first message {got} bytes" if got else ", connected (no message in 2 s)"
    except (socket.timeout, OSError):
        extra = ", connected (no message in 2 s)"
    sock.close()
    return "101 Switching Protocols" + extra, True


def main():
    print("This machine's LAN IPs:", ", ".join(lan_ips()) or "(none found)")
    try:
        base = (sys.argv[1] if len(sys.argv) > 1 else default_url()).rstrip("/")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"No server URL: {e}\nPass one: python tools/net_check.py https://<ip>:8000")
    u = urlparse(base)
    secure = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if secure else 80)
    print(f"Checking {base}")

    try:
        t0 = time.time()
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
        report("DNS", True, f"{host} -> {', '.join(addrs)} ({(time.time() - t0) * 1000:.0f} ms)")
    except OSError as e:
        report("DNS", False, f"{host}: {e}")
        return summary()

    try:
        t0 = time.time()
        socket.create_connection((host, port), timeout=TIMEOUT).close()
        report("TCP", True, f"{host}:{port} connected in {(time.time() - t0) * 1000:.0f} ms")
    except OSError as e:
        report("TCP", False, f"{host}:{port}: {e}  (server down, wrong IP/port, firewall, or Wi-Fi client isolation)")
        return summary()

    verify = True
    try:
        detail, ok = check_http(base, verify=True)
        report("GET /world", ok, detail)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            verify = False
            try:
                detail, ok = check_http(base, verify=False)
                report("GET /world", ok, detail + "  | cert NOT trusted by Python "
                       "(self-signed, or this Python lacks the mkcert root CA; to check with it: "
                       "SSL_CERT_FILE=\"$(mkcert -CAROOT)/rootCA.pem\"). Devices without the CA will show a warning.", warn=True)
            except Exception as e2:  # noqa: BLE001
                report("GET /world", False, str(e2))
        else:
            report("GET /world", False, f"{e}  (wrong scheme? http vs https)")
    except Exception as e:  # noqa: BLE001
        report("GET /world", False, str(e))

    try:
        detail, ok = check_ws(host, port, secure, verify)
        report("WS /tracks", ok, detail)
    except Exception as e:  # noqa: BLE001
        report("WS /tracks", False, str(e))
    return summary()


def summary():
    ok = all(results)
    print("RESULT:", "ALL PASS" if ok else "FAILED (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
