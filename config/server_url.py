"""Pick the fusion-server URL.

Order: SERVER_URL env var  >  config/server.json  ->  "<mode>_url"  (mode = lan | tunnel | usb)

    from config.server_url import server_url, ws_url
    base = server_url()              # e.g. https://172.20.10.3:8000
    ws_url(base, "/ingest")          # wss://172.20.10.3:8000/ingest

CLI: python config/server_url.py   -> prints the chosen URL
"""
import json
import os
from pathlib import Path

CONFIG = Path(__file__).with_name("server.json")
MODES = ("lan", "tunnel", "usb")


def server_url(config_path=CONFIG):
    env = os.environ.get("SERVER_URL", "").strip()
    if env:
        return env.rstrip("/")
    cfg = json.loads(Path(config_path).read_text())
    mode = cfg.get("mode", "lan")
    if mode not in MODES:
        raise ValueError(f"{config_path}: mode must be one of {MODES}, got {mode!r}")
    url = (cfg.get(f"{mode}_url") or "").strip()
    if not url or "<" in url:
        raise ValueError(f"{config_path}: mode={mode!r} but {mode}_url is not set ({url!r})")
    return url.rstrip("/")


def ws_url(base, path):
    """https://h:p + /tracks -> wss://h:p/tracks (http -> ws)."""
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + path


if __name__ == "__main__":
    print(server_url())
