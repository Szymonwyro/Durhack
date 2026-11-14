"""Camera registry for the fusion server: state + weight per camera (docs/CONTRACTS.md "Camera states").

TRUSTED (weight 1.0) / DEGRADED (weight from the worker, default 0.4) contribute detections; EXCLUDED and OFFLINE
contribute nothing. The worker decides TRUSTED/DEGRADED/EXCLUDED (calib/refresh.py) and sends it with every
message. The SERVER decides OFFLINE: socket closed, an OFFLINE status, or no message for > stale_timeout,
whatever the worker last claimed. Workers that don't send a state (older code) count as TRUSTED, with a warning.

    reg = CameraRegistry(["camA", "camB"], stale_timeout=2.5)
    changes = reg.on_message("camA", msg, now)   # -> [(cam, old, new)] state transitions
    changes = reg.on_disconnect("camA", now)
    changes = reg.check_stale(now)               # call a few times a second
    reg.active("camA")                           # True: use its detections
"""
TRUSTED, DEGRADED, EXCLUDED, OFFLINE = "TRUSTED", "DEGRADED", "EXCLUDED", "OFFLINE"
STATES = (TRUSTED, DEGRADED, EXCLUDED, OFFLINE)


class CameraRegistry:
    def __init__(self, camera_ids, stale_timeout=2.5, log=print):
        self.stale_timeout, self.log = float(stale_timeout), log
        self.cams = {c: {"state": OFFLINE, "weight": 0.0, "reasons": ["never connected"], "last_msg": None,
                         "since": None, "legacy": False} for c in camera_ids}

    def _set(self, cam, state, weight, reasons, now):
        c = self.cams.setdefault(cam, {"state": OFFLINE, "weight": 0.0, "reasons": [], "last_msg": None,
                                       "since": None, "legacy": False})
        old = c["state"]
        c.update(state=state, weight=float(weight), reasons=list(reasons))
        if old != state:
            c["since"] = now
            self.log(f"[server] {cam}: {old} -> {state} ({'; '.join(map(str, reasons)) or '-'})")
            return [(cam, old, state)]
        return []

    def on_message(self, cam, msg, now):
        """Any message from the worker (detections or status). Returns state transitions."""
        c = self.cams.setdefault(cam, {"state": OFFLINE, "weight": 0.0, "reasons": [], "last_msg": None,
                                       "since": None, "legacy": False})
        c["last_msg"] = now
        state = msg.get("state")
        if state not in STATES:
            if not c["legacy"]:
                c["legacy"] = True
                self.log(f"[server] WARNING: {cam} sends no camera state (old worker): treating it as TRUSTED")
            state, weight, reasons = TRUSTED, 1.0, ["old worker: no state, assumed TRUSTED"]
        else:
            weight = msg.get("weight")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                weight = {TRUSTED: 1.0, DEGRADED: 0.4}.get(state, 0.0)
            if state in (EXCLUDED, OFFLINE):
                weight = 0.0
            weight = max(0.0, min(1.0, float(weight)))
            reasons = msg.get("reasons") or []
        return self._set(cam, state, weight, reasons, now)

    def on_disconnect(self, cam, now, why="socket closed"):
        if cam in self.cams and self.cams[cam]["state"] != OFFLINE:
            return self._set(cam, OFFLINE, 0.0, [why], now)
        return []

    def check_stale(self, now):
        changes = []
        for cam, c in self.cams.items():
            if c["state"] != OFFLINE and (c["last_msg"] is None or now - c["last_msg"] > self.stale_timeout):
                quiet = "never" if c["last_msg"] is None else f"{now - c['last_msg']:.1f} s"
                changes += self._set(cam, OFFLINE, 0.0, [f"no messages for {quiet}"], now)
        return changes

    def active(self, cam):
        return cam in self.cams and self.cams[cam]["state"] in (TRUSTED, DEGRADED)

    def weight(self, cam):
        return self.cams[cam]["weight"] if self.active(cam) else 0.0

    def snapshot(self, now):
        return {cam: {"state": c["state"], "weight": round(c["weight"], 3), "reasons": c["reasons"][-3:],
                      "since_s": None if c["since"] is None else round(now - c["since"], 1),
                      "last_msg_s": None if c["last_msg"] is None else round(now - c["last_msg"], 2)}
                for cam, c in self.cams.items()}
