"""Project image detections onto the calibrated floor or triangulate them in 3D.

Each camera needs calibrated intrinsics (K, dist) and extrinsics (R, t) in
config/cameras.json. R and t map world coordinates into camera coordinates.
"""

from collections.abc import Mapping, Sequence
from typing import TypedDict

import cv2
import numpy as np

from perception.detect import Detection, FrameDetections


class CameraCalibration(TypedDict):
    id: str
    K: list[list[float]]
    dist: list[float]
    R: list[list[float]]
    t: list[float]


class WorldDetection(TypedDict):
    camera_id: str
    class_id: int
    class_name: str
    confidence: float
    bbox: list[float]
    image_point: list[float]
    position: list[float]


class TriangulatedPoint(TypedDict):
    position: list[float]
    cameras: list[str]
    reprojection_errors_px: dict[str, float]
    mean_reprojection_error_px: float


class TriangulatedDetection(TriangulatedPoint):
    class_id: int
    class_name: str
    confidence_by_camera: dict[str, float]


def pixel_to_floor(
    pixel: Sequence[float],
    camera: CameraCalibration,
) -> tuple[float, float]:
    """Convert an image pixel to world-floor metres using calibrated camera pose."""
    if len(pixel) != 2 or not np.isfinite(pixel).all():
        raise ValueError("pixel must contain two finite image coordinates")

    intrinsic = np.asarray(camera["K"], dtype=np.float64)
    distortion = np.asarray(camera["dist"], dtype=np.float64)
    rotation = np.asarray(camera["R"], dtype=np.float64)
    translation = np.asarray(camera["t"], dtype=np.float64).reshape(-1)
    if intrinsic.shape != (3, 3) or rotation.shape != (3, 3) or translation.shape != (3,):
        raise ValueError("camera K and R must be 3x3; t must contain three values")
    if not all(np.isfinite(value).all() for value in (intrinsic, distortion, rotation, translation)):
        raise ValueError("camera calibration values must be finite")
    if abs(np.linalg.det(rotation)) < 1e-12:
        raise ValueError("camera rotation matrix is singular")

    normalized = cv2.undistortPoints(
        np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2),
        intrinsic,
        distortion,
    ).reshape(2)
    ray_camera = np.array([normalized[0], normalized[1], 1.0], dtype=np.float64)

    # Extrinsics use X_camera = R @ X_world + t.
    camera_origin = -rotation.T @ translation
    ray_world = rotation.T @ ray_camera
    if abs(ray_world[2]) < 1e-9:
        raise ValueError("pixel ray is parallel to the floor plane")

    distance = -camera_origin[2] / ray_world[2]
    if distance <= 0:
        raise ValueError("pixel ray intersects the floor behind the camera")

    world_point = camera_origin + distance * ray_world
    return float(world_point[0]), float(world_point[1])


def triangulate_point(
    image_points: Mapping[str, Sequence[float]],
    cameras: Mapping[str, CameraCalibration],
    max_reprojection_error_px: float | None = None,
) -> TriangulatedPoint:
    """Triangulate one corresponding image point from two or more cameras.

    All points must refer to the same physical point in the same moment.
    Returns the 3D position in world metres and each camera's reprojection
    error, which can be used to judge the result.
    """
    if len(image_points) < 2:
        raise ValueError("at least two camera observations are required")
    if max_reprojection_error_px is not None and max_reprojection_error_px < 0:
        raise ValueError("max_reprojection_error_px must be non-negative")

    rows: list[np.ndarray] = []
    calibrations: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for camera_id, pixel in image_points.items():
        if camera_id not in cameras:
            raise KeyError(f"No calibration configured for camera {camera_id!r}")
        if len(pixel) != 2 or not np.isfinite(pixel).all():
            raise ValueError(f"image point for {camera_id!r} must contain two finite values")

        camera = cameras[camera_id]
        if camera["id"] != camera_id:
            raise ValueError(
                f"Calibration key {camera_id!r} does not match camera ID {camera['id']!r}"
            )
        intrinsic = np.asarray(camera["K"], dtype=np.float64)
        distortion = np.asarray(camera["dist"], dtype=np.float64)
        rotation = np.asarray(camera["R"], dtype=np.float64)
        translation = np.asarray(camera["t"], dtype=np.float64).reshape(-1)
        if intrinsic.shape != (3, 3) or rotation.shape != (3, 3) or translation.shape != (3,):
            raise ValueError(f"invalid K, R, or t shape for camera {camera_id!r}")
        if not all(
            np.isfinite(value).all()
            for value in (intrinsic, distortion, rotation, translation)
        ):
            raise ValueError(f"calibration for camera {camera_id!r} must be finite")
        if abs(np.linalg.det(rotation)) < 1e-12:
            raise ValueError(f"rotation matrix for camera {camera_id!r} is singular")

        normalized = cv2.undistortPoints(
            np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2),
            intrinsic,
            distortion,
        ).reshape(2)
        projection = np.column_stack((rotation, translation))
        rows.extend(
            (
                normalized[0] * projection[2] - projection[0],
                normalized[1] * projection[2] - projection[1],
            )
        )
        calibrations[camera_id] = (intrinsic, distortion, projection)

    _, singular_values, right_vectors = np.linalg.svd(np.asarray(rows))
    if singular_values[-2] <= np.finfo(np.float64).eps * singular_values[0]:
        raise ValueError("camera observations do not provide a stable 3D intersection")
    homogeneous = right_vectors[-1]
    if abs(homogeneous[3]) < 1e-12:
        raise ValueError("camera observations intersect at or near infinity")
    point_3d = homogeneous[:3] / homogeneous[3]
    if not np.isfinite(point_3d).all():
        raise ValueError("triangulation produced a non-finite 3D position")

    reprojection_errors: dict[str, float] = {}
    for camera_id, (intrinsic, distortion, projection) in calibrations.items():
        camera_point = projection[:, :3] @ point_3d + projection[:, 3]
        if camera_point[2] <= 0:
            raise ValueError(f"triangulated point is behind camera {camera_id!r}")

        rotation_vector, _ = cv2.Rodrigues(projection[:, :3])
        projected, _ = cv2.projectPoints(
            point_3d.reshape(1, 1, 3),
            rotation_vector,
            projection[:, 3],
            intrinsic,
            distortion,
        )
        projected_pixel = projected.reshape(2)
        reprojection_errors[camera_id] = float(
            np.linalg.norm(projected_pixel - np.asarray(image_points[camera_id]))
        )

    mean_error = float(np.mean(list(reprojection_errors.values())))
    if (
        max_reprojection_error_px is not None
        and max(reprojection_errors.values()) > max_reprojection_error_px
    ):
        raise ValueError(
            f"reprojection error {max(reprojection_errors.values()):.2f}px exceeds "
            f"the {max_reprojection_error_px:.2f}px limit"
        )

    return {
        "position": [float(value) for value in point_3d],
        "cameras": list(image_points),
        "reprojection_errors_px": reprojection_errors,
        "mean_reprojection_error_px": mean_error,
    }


def triangulate_detections(
    detections: Mapping[str, Detection],
    cameras: Mapping[str, CameraCalibration],
    max_reprojection_error_px: float | None = None,
) -> TriangulatedDetection:
    """Triangulate detections already matched as the same object across cameras.

    Uses each detector's bounding-box center (`position`) as the corresponding
    image point. For more precise 3D points, pass matched landmarks to
    `triangulate_point` instead.
    """
    if len(detections) < 2:
        raise ValueError("at least two camera detections are required")

    first_detection = next(iter(detections.values()))
    for camera_id, detection in detections.items():
        if detection["class_id"] != first_detection["class_id"]:
            raise ValueError(f"detections do not share a class (camera {camera_id!r})")

    point = triangulate_point(
        {camera_id: detection["position"] for camera_id, detection in detections.items()},
        cameras,
        max_reprojection_error_px=max_reprojection_error_px,
    )
    return {
        **point,
        "class_id": first_detection["class_id"],
        "class_name": first_detection["class_name"],
        "confidence_by_camera": {
            camera_id: detection["confidence"] for camera_id, detection in detections.items()
        },
    }


def project_detection(
    detection: Detection,
    camera: CameraCalibration,
) -> WorldDetection:
    """Project one detect.py result onto the floor.

    Uses the detector's person foot point when available. Otherwise, assumes
    the object's floor contact is at the bottom-centre of its bounding box.
    """
    x, y, width, height = detection["bbox"]
    image_point = detection["foot"]
    if image_point is None:
        image_point = [x + width / 2, y + height]

    world_x, world_y = pixel_to_floor(image_point, camera)
    return {
        "camera_id": camera["id"],
        "class_id": detection["class_id"],
        "class_name": detection["class_name"],
        "confidence": detection["confidence"],
        "bbox": detection["bbox"],
        "image_point": [float(image_point[0]), float(image_point[1])],
        "position": [world_x, world_y],
    }


def project_frame(
    frame: FrameDetections,
    camera: CameraCalibration,
) -> list[WorldDetection]:
    """Project all detections from one camera frame to world-floor coordinates."""
    return [project_detection(detection, camera) for detection in frame["detections"]]


def project_cameras(
    frames: Mapping[str, FrameDetections],
    cameras: Mapping[str, CameraCalibration],
) -> dict[str, list[WorldDetection]]:
    """Project a frame from each camera independently, keyed by camera ID.

    Callers can pass results from separate ``detect_video`` camera streams.
    Cross-camera identity matching/fusion is intentionally left to the tracker;
    detector outputs do not contain a shared object ID.
    """
    projected: dict[str, list[WorldDetection]] = {}
    for camera_id, frame in frames.items():
        if camera_id not in cameras:
            raise KeyError(f"No calibration configured for camera {camera_id!r}")
        camera = cameras[camera_id]
        if camera["id"] != camera_id:
            raise ValueError(
                f"Calibration key {camera_id!r} does not match camera ID {camera['id']!r}"
            )
        projected[camera_id] = project_frame(frame, camera)
    return projected
