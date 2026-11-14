"""calib/refresh.py decide_refresh: the 6 rules, rule A, jitter, DEGRADED, reconnect.   python tests/test_refresh.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from calib.refresh import DEFAULTS, DEGRADED, EXCLUDED, TRUSTED, decide_refresh, format_log  # noqa: E402

TH = dict(DEFAULTS, pixel_tol=5.0, reproj_thresh=2.0)  # spec values, so the numbers below are easy to read


def sq(x, y, s=40):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]


EXPECTED = {1: sq(100, 100), 7: sq(600, 100), 3: sq(600, 500), 5: sq(100, 500)}


def shifted(ids, dx):
    return {i: [[p[0] + (dx if i in ids else 0), p[1]] for p in EXPECTED[i]] for i in EXPECTED}


def vis(ids, moved=(), dx=12):
    s = shifted(set(moved), dx)
    return {i: s[i] for i in ids}


def solve(err, ids=(1, 3, 5, 7), dm=0.0, dd=0.0):
    return {"ids": list(ids), "err": err, "delta_m": dm, "delta_deg": dd}


def d(visible, solve_result=None, displaced=(), prev=TRUSTED, ctx="routine", expected=EXPECTED):
    return decide_refresh(visible, expected, set(displaced), solve_result, prev, ctx, TH)


def test_rule1_four_visible():
    r = d(vis([1, 3, 5, 7]), solve(1.2, dm=0.03))
    assert (r.action, r.state, r.weight, r.branch) == ("replace_pose", TRUSTED, 1.0, "1:solve4")
    r = d(vis([1, 3, 5, 7], moved=[1, 3, 5, 7]), solve(1.5, dm=0.2))
    assert r.action == "replace_pose" and r.state == TRUSTED


def test_jitter_rule():
    r = d(vis([1, 3, 5, 7]), solve(1.2, dm=0.004, dd=0.2))
    assert (r.action, r.state, r.branch) == ("keep", TRUSTED, "1:solve4-same")
    r = d(vis([1, 3, 5, 7]), solve(1.2, dm=0.004, dd=0.8))   # rotation over eps -> replace
    assert r.action == "replace_pose"


def test_rule1_bad_solve():
    r = d(vis([1, 3, 5, 7]), solve(3.5))                       # unmoved, solve poor -> keep
    assert (r.action, r.state) == ("keep", TRUSTED)
    r = d(vis([1, 3, 5, 7], moved=[1, 3, 5, 7]), solve(3.5, dm=0.3))
    assert (r.action, r.state, r.weight) == ("replace_pose", DEGRADED, TH["degraded_weight"])


def test_rule2_three_unmoved():
    r = d(vis([1, 3, 5]))
    assert (r.action, r.state, r.branch) == ("keep", TRUSTED, "2:three-unmoved")


def test_rule3_three_moved():
    r = d(vis([1, 3, 5], moved=[1, 3, 5]), solve(1.4, ids=(1, 3, 5), dm=0.1))
    assert (r.action, r.state, r.branch) == ("replace_pose", TRUSTED, "3:solve3")
    r = d(vis([1, 3, 5], moved=[1, 3, 5]), solve(3.1, ids=(1, 3, 5), dm=0.1))
    assert (r.action, r.state, r.branch) == ("replace_pose", DEGRADED, "3:solve3-poor")
    r = d(vis([1, 3, 5], moved=[1, 3]), solve(1.4, ids=(1, 3, 5)))   # majority (2/3) moved -> camera
    assert r.branch == "3:solve3"


def test_rule4_few_unmoved():
    for ids in ([1], [1, 7]):
        r = d(vis(ids))
        assert (r.action, r.state, r.branch) == ("keep", TRUSTED, "4:few-unmoved")


def test_rule5_few_moved():
    r = d(vis([1], moved=[1]))
    assert (r.action, r.state, r.weight, r.branch) == ("exclude", EXCLUDED, 0.0, "5:few-moved")
    r = d(vis([1, 7], moved=[7]))                              # 1 of 2: not a strict majority -> rule 5
    assert r.branch == "5:few-moved" and r.next_in_s == TH["excluded_retry_s"]


def test_rule6_none():
    r = d({})
    assert (r.action, r.state, r.branch) == ("exclude", EXCLUDED, "6:none")


def test_ruleA_kicked_marker():
    r = d(vis([1, 3, 5, 7], moved=[3]), solve(4.0))          # one kicked among 4
    assert (r.action, r.state, r.branch) == ("flag_displaced", TRUSTED, "A:marker-displaced")
    assert r.displaced == {3}
    r = d(vis([1, 5, 7], moved=[7]))                           # one kicked among 3
    assert r.action == "flag_displaced" and r.displaced == {7}
    r = d(vis([1, 3, 5, 7], moved=[3]), displaced={3})         # still off: stays flagged, others decide
    assert 3 in r.displaced and r.branch == "2:three-unmoved"
    r = d(vis([1, 3, 5, 7]), solve(1.0), displaced={3})       # put back: re-admitted
    assert r.displaced == set() and r.branch.startswith("1:")
    r = d(vis([1, 3, 5, 7], moved=[3]), solve(4.0), prev=DEGRADED)  # keeps previous state
    assert r.state == DEGRADED


def test_reconnect():
    r = d(vis([5]), ctx="reconnect", prev="OFFLINE")
    assert (r.action, r.state, r.branch) == ("keep", TRUSTED, "reconnect:verified")
    r = d({}, ctx="reconnect", prev="OFFLINE")
    assert (r.state, r.branch, r.next_in_s) == (DEGRADED, "reconnect:no-markers", TH["verify_retry_s"])
    r = d(vis([1, 3, 5], moved=[1, 3, 5]), solve(1.3, ids=(1, 3, 5), dm=0.2), ctx="reconnect", prev="OFFLINE")
    assert (r.action, r.state, r.branch) == ("replace_pose", TRUSTED, "3:solve3")
    r = d(vis([1, 3, 5, 7]), None, ctx="reconnect", expected=None)     # no stored pose, no solve
    assert (r.state, r.branch) == (EXCLUDED, "no-pose")
    r = d(vis([1, 3, 5, 7]), solve(1.1), ctx="reconnect", expected=None)
    assert (r.action, r.state) == ("replace_pose", TRUSTED)
    r = d(vis([1, 3, 5]), solve(1.1, ids=(1, 3, 5)), ctx="reconnect", expected=None)  # 3 not enough w/o pose
    assert r.state == EXCLUDED


def test_log_line():
    r = d(vis([1, 3, 5, 7], moved=[3]), solve(4.0))
    line = format_log("camA", r, [1, 3, 5, 7])
    assert "3:12.0px*" in line and "A:marker-displaced" in line and "TRUSTED" in line


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print(format_log("camA", d(vis([1, 3, 5, 7], moved=[3]), solve(4.0)), [1, 3, 5, 7]))
    print("all refresh tests passed")
