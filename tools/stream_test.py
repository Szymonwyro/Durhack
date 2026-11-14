#!/usr/bin/env python3
"""Network smoke test: can this Wi-Fi carry camera video between devices?

Prep tool, not project code. It serves JPEG frames over plain HTTP and measures FPS, bandwidth and
round-trip time on the receiving device.

  serve:  python tools/stream_test.py serve [--source 1] [--width 1280] [--quality 80] [--port 8090]
          (falls back to synthetic frames if the camera can't be opened)
  camera: python tools/stream_test.py serve --source 0 --raw --quality 90
          -> use http://<ip>:8090/stream.mjpg as the camera "source" on Mac-1 (live_map, calib)
  push to Mac-1 (only OUTBOUND from the camera laptop, so no Windows firewall changes):
          Mac-1:   python tools/stream_test.py relay                 (port 8095)
          laptop:  python tools/stream_test.py push http://<mac1-ip>:8095 --cam camB --source 0
          -> camera source on Mac-1: http://127.0.0.1:8095/camB/stream.mjpg
  recv:   python tools/stream_test.py recv http://<ip>:8090 [--seconds 10]   (e.g. from Mac-2 / UNO Q)
  phone:  open http://<ip>:8090/ in iPhone Safari or the Quest browser; the page shows live stats.

Endpoints: /  test page | /frame.jpg  latest frame (pull) | /stream.mjpg  MJPEG (push) | /stats  JSON
Rough guide: >=15 fps and RTT <100 ms at 1280 px means streaming video over this network is fine.
0 fps or a timeout from another device means the network blocks device-to-device traffic (client isolation).
"""
import argparse
import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Stream test</title>
<style>body{font:16px -apple-system,system-ui,sans-serif;margin:16px;background:#111;color:#eee}
img{max-width:100%;display:block;margin:8px 0;background:#000}#s{font:20px ui-monospace,monospace;white-space:pre}
.ok{color:#5f5}.bad{color:#f55}</style></head><body>
<b>Wi-Fi video stream test</b> (pulls each new camera frame; max = camera fps)
<img id="v"><div id="s">starting...</div>
<script>
const v=document.getElementById('v'),s=document.getElementById('s');
let n=0,bytes=0,rtts=[],t0=performance.now(),fails=0,seq=-1;
async function loop(){
  for(;;){
    const a=performance.now();
    try{
      const r=await fetch('/frame.jpg?after='+seq,{cache:'no-store'}); const b=await r.blob();
      seq=+r.headers.get('X-Seq');
      rtts.push(performance.now()-a); bytes+=b.size; n++;
      const u=URL.createObjectURL(b); v.onload=()=>URL.revokeObjectURL(u); v.src=u;
    }catch(e){fails++; await new Promise(r=>setTimeout(r,500));}
  }
}
setInterval(()=>{
  const dt=(performance.now()-t0)/1000, fps=n/dt, mbps=bytes*8/dt/1e6;
  rtts.sort((a,b)=>a-b); const med=rtts.length?rtts[rtts.length>>1]:0, p95=rtts.length?rtts[Math.floor(rtts.length*.95)]:0;
  const good=fps>=15&&med<100;
  s.innerHTML=`<span class="${good?'ok':'bad'}">${good?'GOOD':'CHECK'}</span>\\nfps   ${fps.toFixed(1)}\\nMbps  ${mbps.toFixed(1)}\\nRTT   median ${med.toFixed(0)} ms, p95 ${p95.toFixed(0)} ms\\nfails ${fails}`;
  n=0;bytes=0;rtts=[];t0=performance.now();
},2000);
loop();
</script></body></html>"""


class Source:
    """Grabs frames in a background thread; keeps only the latest JPEG."""

    def __init__(self, src, width, quality, raw=False, height=None):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        self.width, self.quality, self.raw = width, quality, raw
        self.jpg, self.seq, self.lock = None, 0, threading.Condition()
        self.cap = cv2.VideoCapture(src)
        if height:  # ask the camera for the calibrated size (Windows often defaults to 640x480)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        ok, first = self.cap.read() if self.cap.isOpened() else (False, None)
        if ok and height and (first.shape[1], first.shape[0]) != (width, height):
            print(f"WARNING: camera gives {first.shape[1]}x{first.shape[0]}, not {width}x{height}; "
                  f"frames will be resized and calibration (K) won't match", flush=True)
        if not ok and raw:
            raise SystemExit(f"camera {src} unavailable (wrong --source? Camera permission? in use by another app?)")
        self.kind = f"camera {src}" if ok else "synthetic frames (camera unavailable)"
        if not ok:
            self.cap.release()
            self.cap = None
        threading.Thread(target=self._run, daemon=True).start()

    def _frame(self):
        cv2, np = self.cv2, self.np
        if self.cap is not None:
            ok, f = self.cap.read()
            if ok:
                return f
        h = self.width * 9 // 16
        f = (np.random.default_rng().integers(0, 255, (h // 8, self.width // 8, 3), dtype=np.uint8))
        f = cv2.resize(f, (self.width, h), interpolation=cv2.INTER_NEAREST)  # realistic JPEG size
        x = int((time.time() * 200) % self.width)
        cv2.rectangle(f, (x, h // 3), (x + 80, h // 3 + 80), (0, 255, 255), -1)
        time.sleep(1 / 30)
        return f

    def _run(self):
        cv2 = self.cv2
        while True:
            f = self._frame()
            if f.shape[1] != self.width:
                f = cv2.resize(f, (self.width, f.shape[0] * self.width // f.shape[1]))
            if not self.raw:
                cv2.putText(f, f"{time.time():.3f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok:
                with self.lock:
                    self.jpg, self.seq = buf.tobytes(), self.seq + 1
                    self.lock.notify_all()

    def next(self, last_seq, timeout=2.0):
        with self.lock:
            self.lock.wait_for(lambda: self.seq != last_seq, timeout)
            return self.jpg, self.seq


def serve(args):
    src = Source(args.source, args.width, args.quality, args.raw, args.height)
    served = {"frames": 0, "bytes": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            try:
                if path == "/":
                    self._send(PAGE, "text/html; charset=utf-8")
                elif path == "/frame.jpg":
                    # ?after=<seq> waits for a newer frame than the client already has
                    jpg, seq = src.next(int(q.get("after", -1)))
                    served["frames"] += 1
                    served["bytes"] += len(jpg)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Seq", str(seq))
                    self.send_header("Access-Control-Expose-Headers", "X-Seq")
                    self.end_headers()
                    self.wfile.write(jpg)
                elif path == "/stats":
                    self._send(json.dumps({"source": src.kind, "width": args.width, **served}).encode(), "application/json")
                elif path == "/stream.mjpg":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                    self.end_headers()
                    seq = 0
                    while True:
                        jpg, seq = src.next(seq)
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

    while src.jpg is None:
        time.sleep(0.05)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    s.close()
    print(f"source: {src.kind}, {args.width}px, JPEG q{args.quality}, ~{len(src.jpg) / 1024:.0f} KB/frame", flush=True)
    print(f"open on another device:  http://{ip}:{args.port}/      (Ctrl+C to stop)", flush=True)
    print(f"as a camera source (cameras.json / --source):  http://{ip}:{args.port}/stream.mjpg", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), H).serve_forever()


def recv(args):
    url = args.url.rstrip("/") + "/frame.jpg"
    n = nbytes = fails = 0
    seq = -1
    rtts = []
    t_end = time.time() + args.seconds
    t0 = time.time()
    while time.time() < t_end:
        a = time.time()
        try:
            with urllib.request.urlopen(f"{url}?after={seq}", timeout=3) as r:
                nbytes += len(r.read())
                seq = int(r.headers.get("X-Seq", -1))
            rtts.append((time.time() - a) * 1000)
            n += 1
        except OSError as e:
            fails += 1
            if fails == 1:
                print("request failed:", e)
            if fails >= 5 and n == 0:
                break
    dt = time.time() - t0
    rtts.sort()
    if not n:
        print(f"FAIL: no frames from {url} ({fails} failed requests). Server down, wrong IP, or client isolation.")
        return 1
    fps, mbps = n / dt, nbytes * 8 / dt / 1e6
    med, p95 = rtts[len(rtts) // 2], rtts[int(len(rtts) * 0.95)]
    good = fps >= 15 and med < 100
    print(f"{'GOOD' if good else 'CHECK'}: {fps:.1f} fps, {mbps:.1f} Mbps, RTT median {med:.0f} ms / p95 {p95:.0f} ms, "
          f"{nbytes / n / 1024:.0f} KB/frame, {fails} failures over {dt:.1f} s")
    return 0 if good else 1



class Latest:
    """Latest JPEG per camera name, with a condition so readers wait for new frames."""

    def __init__(self):
        self.frames, self.seqs, self.times, self.lock = {}, {}, {}, threading.Condition()

    def put(self, cam, jpg):
        with self.lock:
            self.frames[cam], self.seqs[cam], self.times[cam] = jpg, self.seqs.get(cam, 0) + 1, time.time()
            self.lock.notify_all()

    def next(self, cam, last_seq, timeout=2.0):
        with self.lock:
            self.lock.wait_for(lambda: self.seqs.get(cam, 0) not in (0, last_seq), timeout)
            return self.frames.get(cam), self.seqs.get(cam, 0)


def relay(args):
    """Receive frames POSTed by `push` (e.g. from the Windows laptop) and re-serve them as MJPEG.
    Mac-1 then uses http://127.0.0.1:<port>/<cam>/stream.mjpg as the camera source."""
    store = Latest()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, so the pusher reuses one connection

        def log_message(self, *a):
            pass

        def do_POST(self):
            parts = self.path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "push":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if body:
                store.put(parts[1], body)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].strip("/")
            try:
                if path == "":
                    now = time.time()
                    status = {c: {"frames": store.seqs[c], "age_s": round(now - store.times[c], 2)} for c in store.seqs}
                    body = json.dumps(status).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path.endswith("/stream.mjpg"):
                    cam = path.split("/")[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    seq = 0
                    while True:
                        jpg, seq2 = store.next(cam, seq)
                        if jpg is None or seq2 == seq:
                            continue
                        seq = seq2
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg)
                                         + jpg + b"\r\n")
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    s.close()
    print(f"relay listening on port {args.port}", flush=True)
    print(f"on the camera laptop:  python tools/stream_test.py push http://{ip}:{args.port} --cam camB --source <N>",
          flush=True)
    print(f"camera source on this Mac:  http://127.0.0.1:{args.port}/<cam>/stream.mjpg   status: http://127.0.0.1:{args.port}/",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), H).serve_forever()


def push(args):
    """Capture a camera here and POST each JPEG to a relay on Mac-1 (outbound only: no firewall changes)."""
    import http.client
    from urllib.parse import urlparse

    src = Source(args.source, args.width, args.quality, raw=True, height=args.height)
    u = urlparse(args.url)
    path = f"/push/{args.cam}"
    print(f"pushing camera {args.source} as {args.cam} to {u.hostname}:{u.port or 80} (Ctrl+C to stop)", flush=True)
    conn, seq, sent, t0, errors = None, 0, 0, time.time(), 0
    while True:
        jpg, seq = src.next(seq)
        try:
            if conn is None:
                conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
            conn.request("POST", path, body=jpg, headers={"Content-Type": "image/jpeg"})
            conn.getresponse().read()
            sent += 1
            errors = 0
        except OSError as e:
            errors += 1
            if conn is not None:
                conn.close()
            conn = None
            if errors == 1 or errors % 20 == 0:
                print(f"push failed ({e}); retrying... (relay running on the Mac? same Wi-Fi?)", flush=True)
            time.sleep(0.5)
        if time.time() - t0 >= 5:
            print(f"{sent / (time.time() - t0):.1f} fps sent, ~{len(jpg) / 1024:.0f} KB/frame", flush=True)
            sent, t0 = 0, time.time()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--source", type=int, default=1, help="OpenCV camera index (default 1 = MacBook camera here)")
    s.add_argument("--width", type=int, default=1280)
    s.add_argument("--height", type=int, default=720, help="requested capture height (0 = camera default)")
    s.add_argument("--quality", type=int, default=80)
    s.add_argument("--port", type=int, default=8090)
    s.add_argument("--raw", action="store_true",
                   help="no timestamp overlay: use when the stream is a real camera source (detection/calibration)")
    rl = sub.add_parser("relay", help="on Mac-1: receive pushed frames, re-serve as MJPEG")
    rl.add_argument("--port", type=int, default=8095)
    pu = sub.add_parser("push", help="on the camera laptop: send camera frames to a relay")
    pu.add_argument("url", help="relay address, e.g. http://10.247.46.145:8095")
    pu.add_argument("--cam", default="camB")
    pu.add_argument("--source", type=int, default=0)
    pu.add_argument("--width", type=int, default=1280)
    pu.add_argument("--height", type=int, default=720)
    pu.add_argument("--quality", type=int, default=90)
    r = sub.add_parser("recv")
    r.add_argument("url")
    r.add_argument("--seconds", type=float, default=10)
    a = p.parse_args()
    sys.exit({"serve": serve, "recv": recv, "relay": relay, "push": push}[a.cmd](a))


if __name__ == "__main__":
    main()
