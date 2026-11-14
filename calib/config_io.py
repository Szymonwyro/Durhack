"""Read/write config/cameras.json and config/markers.json (Role 3).

cameras.json: {"cameras": [{"id": "camA", "source": 0, "width": 1280, "height": 720,
                            "K": 3x3, "dist": [...], "R": 3x3, "t": [3],   # world -> camera
                            "pos": [x,y,z], "yaw": deg, "fov": deg, ...}]}
Each calibration script updates only its own camera's entry, so different cameras merge cleanly in git.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMERAS = ROOT / "config" / "cameras.json"
MARKERS = ROOT / "config" / "markers.json"


def _dump(obj):
    """Pretty JSON with short number lists kept on one line (readable 3x3 matrices)."""
    text = json.dumps(obj, indent=2)
    return re.sub(r"\[\s+([-0-9.eE,\s]+?)\s+\]", lambda m: "[" + re.sub(r"\s+", " ", m.group(1)) + "]", text) + "\n"


def load_cameras(path=CAMERAS):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {"cameras": []}


def get_camera(cam_id, path=CAMERAS):
    for cam in load_cameras(path)["cameras"]:
        if cam["id"] == cam_id:
            return cam
    return None


def update_camera(cam_id, path=CAMERAS, **fields):
    """Create or update one camera entry; leaves the other cameras untouched."""
    data = load_cameras(path)
    cam = next((c for c in data["cameras"] if c["id"] == cam_id), None)
    if cam is None:
        cam = {"id": cam_id}
        data["cameras"].append(cam)
    cam.update(fields)
    Path(path).write_text(_dump(data))
    return cam


def load_markers(path=MARKERS):
    return json.loads(Path(path).read_text())


def parse_source(src):
    """'0' -> 0 (OpenCV index); URLs/paths stay strings."""
    if isinstance(src, str) and src.strip().lstrip("-").isdigit():
        return int(src)
    return src
