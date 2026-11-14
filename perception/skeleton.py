"""Moved to perception/pose.py. This shim keeps existing imports working."""
from perception.pose import *  # noqa: F401,F403
from perception.pose import (  # noqa: F401
    ANKLE_FOOT_CONF, BONES, JOINT_MIN_CONF, JOINT_NAMES, MIN_BBOX_HEIGHT_PX, MIN_VALID_JOINTS,
    billboard_plane, camera_centre, foot_from_ankles, lift_keypoints, lift_pixels,
)
