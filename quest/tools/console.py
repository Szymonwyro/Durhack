"""Stream the Quest browser console to this terminal via the DevTools protocol.

    ./quest/dev.sh                          # sets up adb forward tcp:9222
    python3 quest/tools/console.py          # stream console + exceptions
    python3 quest/tools/console.py --reload # reload the page first, then stream
    python3 quest/tools/console.py --reload --once   # reload and exit

Falls back to `adb logcat -s chromium` if the DevTools socket can't be reached.
"""
import argparse
import datetime
import json
import subprocess
import sys
import time
import urllib.request

DEVTOOLS = "http://localhost:9222"
MATCH = "localhost:8001/quest/"


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def say(*a):
    print(ts(), *a, flush=True)


def find_tab():
    with urllib.request.urlopen(DEVTOOLS + "/json", timeout=3) as r:
        tabs = json.load(r)
    pages = [t for t in tabs if t.get("type") == "page" and MATCH in t.get("url", "")]
    if not pages:
        raise RuntimeError("no tab matching " + MATCH + " (open tabs: " +
                           ", ".join(t.get("url", "?") for t in tabs) + ")")
    return pages[0]


def fmt_arg(a):
    if "value" in a:
        return a["value"] if isinstance(a["value"], str) else json.dumps(a["value"])
    return a.get("description") or a.get("unserializableValue") or a.get("type", "?")


def stream(reload, once):
    import websocket  # pip install websocket-client

    tab = find_tab()
    say("attached to", tab["url"])
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=None, suppress_origin=True)
    msg_id = 0

    def send(method, params=None):
        nonlocal msg_id
        msg_id += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))

    send("Runtime.enable")
    send("Log.enable")
    if reload:
        send("Page.reload", {"ignoreCache": True})
        say("reloaded page")
        if once:
            time.sleep(0.5)
            return
    while True:
        m = json.loads(ws.recv())
        meth = m.get("method")
        p = m.get("params", {})
        if meth == "Runtime.consoleAPICalled":
            say(f"[{p.get('type')}]", " ".join(fmt_arg(a) for a in p.get("args", [])))
        elif meth == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            desc = d.get("exception", {}).get("description") or d.get("text")
            say("[EXCEPTION]", desc, f"({d.get('url', '')}:{d.get('lineNumber')})")
        elif meth == "Log.entryAdded":
            e = p.get("entry", {})
            say(f"[log:{e.get('level')}]", e.get("text"), e.get("url", ""))
        elif meth == "Runtime.executionContextsCleared":
            say("--- page navigated/reloaded ---")


def logcat():
    say("DevTools socket not available -> falling back to `adb logcat -s chromium`")
    subprocess.run(["adb", "logcat", "-s", "chromium"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reload", action="store_true", help="reload the page before streaming")
    ap.add_argument("--once", action="store_true", help="with --reload: reload and exit")
    args = ap.parse_args()
    try:
        stream(args.reload, args.once)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        say("DevTools error:", repr(e))
        if args.once:
            sys.exit(1)
        logcat()
