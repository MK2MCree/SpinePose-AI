"""Small, dependency-light reproductions of audited third-party mechanisms.

These functions intentionally reproduce only the portions that can be
independently verified with local fixtures.  They are *not* substitutes for
RepDetect's Android/ML Kit classifier or OpenCap's OpenSim pipeline, and every
result is marked non-diagnostic by the calling application.

The source provenance is pinned in ``third_party_eval_20260828``:

* RepDetect ``50f990f...``: confidence hysteresis used by RepetitionCounter.
* OpenCap Core ``4c0aab...``: calibrated multi-view 2D-to-3D reconstruction
  quality gate before its OpenSim processing stage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ClassifiedPoseFrame:
    """One already-classified pose frame.

    ``confidence`` is deliberately supplied by a local pose classifier.  This
    module never pretends to derive a clinical movement label from raw pixels.
    """

    timestamp_seconds: float
    confidence: float
    tracked_landmark_visibility: float
    framing_complete: bool = True


@dataclass(frozen=True)
class RepDetectTraceResult:
    class_name: str
    repetitions: int
    accepted_frames: int
    rejected_frames: int
    quality_score: float | None
    state: str
    non_diagnostic: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RepDetectRepetitionCounter:
    """Faithful normalized reproduction of RepDetect's RepetitionCounter.

    The upstream code enters a pose only when confidence is strictly greater
    than 6/10, and counts one repetition only when it subsequently falls
    strictly below 4/10.  We normalize that public contract to 0..1 but retain
    the strict comparisons and its enter/leave state machine.
    """

    def __init__(self, class_name: str, enter_threshold: float = 0.6, exit_threshold: float = 0.4):
        if not class_name.strip():
            raise ValueError("class_name is required")
        if not 0 <= exit_threshold < enter_threshold <= 1:
            raise ValueError("expected 0 <= exit_threshold < enter_threshold <= 1")
        self.class_name = class_name
        self.enter_threshold = float(enter_threshold)
        self.exit_threshold = float(exit_threshold)
        self.pose_entered = False
        self.num_repeats = 0

    def observe(self, confidence: float) -> int:
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and within [0, 1]")
        if not self.pose_entered:
            self.pose_entered = confidence > self.enter_threshold
            return self.num_repeats
        if confidence < self.exit_threshold:
            self.num_repeats += 1
            self.pose_entered = False
        return self.num_repeats


_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12
_LEFT_ELBOW = 13
_RIGHT_ELBOW = 14
_LEFT_WRIST = 15
_RIGHT_WRIST = 16
_LEFT_HIP = 23
_RIGHT_HIP = 24
_LEFT_KNEE = 25
_RIGHT_KNEE = 26
_LEFT_ANKLE = 27
_RIGHT_ANKLE = 28
_LANDMARK_COUNT = 33


@dataclass(frozen=True)
class ReferencePoseSample:
    """A local, reviewed reference pose used by the RepDetect-style KNN stage."""

    name: str
    class_name: str
    landmarks: Sequence[Sequence[float]]


@dataclass(frozen=True)
class LandmarkPoseFrame:
    """One local pose-estimator result, without image pixels or identity data."""

    timestamp_seconds: float
    landmarks: Sequence[Sequence[float]]
    tracked_landmark_visibility: float
    framing_complete: bool = True


def _landmark_array(landmarks: Sequence[Sequence[float]]) -> np.ndarray:
    array = np.asarray(landmarks, dtype=float)
    if array.shape != (_LANDMARK_COUNT, 3) or not np.isfinite(array).all():
        raise ValueError("RepDetect-style classification requires 33 finite XYZ landmarks")
    return array


def repdetect_pose_embedding(landmarks: Sequence[Sequence[float]]) -> np.ndarray:
    """Reproduce the fixed 23-vector embedding from RepDetect PoseEmbedding.

    This mirrors only the public geometry/normalization algorithm.  It does not
    reuse the upstream fitness sample labels; production reference samples must
    be clinically reviewed before they are ever made available to a patient.
    """

    normalized = _landmark_array(landmarks).copy()
    hips_center = (normalized[_LEFT_HIP] + normalized[_RIGHT_HIP]) / 2.0
    normalized -= hips_center
    shoulders_center = (normalized[_LEFT_SHOULDER] + normalized[_RIGHT_SHOULDER]) / 2.0
    torso_size = float(np.linalg.norm((hips_center - shoulders_center)[:2]))
    max_distance = max(torso_size * 2.5, float(np.max(np.linalg.norm(normalized[:, :2], axis=1))))
    if max_distance <= 1e-12:
        raise ValueError("pose scale is degenerate")
    normalized *= 100.0 / max_distance

    average = lambda first, second: (normalized[first] + normalized[second]) / 2.0
    subtract = lambda first, second: first - second
    vectors = [
        subtract(average(_LEFT_HIP, _RIGHT_HIP), average(_LEFT_SHOULDER, _RIGHT_SHOULDER)),
        subtract(normalized[_LEFT_SHOULDER], normalized[_LEFT_ELBOW]),
        subtract(normalized[_RIGHT_SHOULDER], normalized[_RIGHT_ELBOW]),
        subtract(normalized[_LEFT_ELBOW], normalized[_LEFT_WRIST]),
        subtract(normalized[_RIGHT_ELBOW], normalized[_RIGHT_WRIST]),
        subtract(normalized[_LEFT_HIP], normalized[_LEFT_KNEE]),
        subtract(normalized[_RIGHT_HIP], normalized[_RIGHT_KNEE]),
        subtract(normalized[_LEFT_KNEE], normalized[_LEFT_ANKLE]),
        subtract(normalized[_RIGHT_KNEE], normalized[_RIGHT_ANKLE]),
        subtract(normalized[_LEFT_SHOULDER], normalized[_LEFT_WRIST]),
        subtract(normalized[_RIGHT_SHOULDER], normalized[_RIGHT_WRIST]),
        subtract(normalized[_LEFT_HIP], normalized[_LEFT_ANKLE]),
        subtract(normalized[_RIGHT_HIP], normalized[_RIGHT_ANKLE]),
        subtract(normalized[_LEFT_HIP], normalized[_LEFT_WRIST]),
        subtract(normalized[_RIGHT_HIP], normalized[_RIGHT_WRIST]),
        subtract(normalized[_LEFT_SHOULDER], normalized[_LEFT_ANKLE]),
        subtract(normalized[_RIGHT_SHOULDER], normalized[_RIGHT_ANKLE]),
        subtract(normalized[_LEFT_HIP], normalized[_LEFT_WRIST]),
        subtract(normalized[_RIGHT_HIP], normalized[_RIGHT_WRIST]),
        subtract(normalized[_LEFT_ELBOW], normalized[_RIGHT_ELBOW]),
        subtract(normalized[_LEFT_KNEE], normalized[_RIGHT_KNEE]),
        subtract(normalized[_LEFT_WRIST], normalized[_RIGHT_WRIST]),
        subtract(normalized[_LEFT_ANKLE], normalized[_RIGHT_ANKLE]),
    ]
    # The upstream implementation intentionally repeats the left/right hip to
    # wrist vectors in its five-joint group.  Preserve that fixed embedding.
    return np.asarray(vectors, dtype=float)


class RepDetectKnnClassifier:
    """Source-faithful two-stage outlier-filtered KNN used by RepDetect.

    Distances use a max-distance top-K gate, followed by a mean-distance top-K
    vote.  Horizontal mirroring is evaluated alongside the original landmarks.
    """

    def __init__(
        self,
        samples: Iterable[ReferencePoseSample],
        *,
        max_distance_top_k: int = 30,
        mean_distance_top_k: int = 10,
        axes_weights: Sequence[float] = (1.0, 1.0, 0.2),
    ):
        if max_distance_top_k < 1 or mean_distance_top_k < 1:
            raise ValueError("top-K values must be positive")
        weights = np.asarray(axes_weights, dtype=float)
        if weights.shape != (3,) or not np.isfinite(weights).all() or (weights < 0).any():
            raise ValueError("axes_weights must be three finite nonnegative values")
        prepared: list[tuple[str, np.ndarray]] = []
        for sample in samples:
            if not sample.name.strip() or not sample.class_name.strip():
                raise ValueError("reference sample name and class are required")
            prepared.append((sample.class_name, repdetect_pose_embedding(sample.landmarks)))
        if not prepared:
            raise ValueError("at least one reference sample is required")
        self._samples = prepared
        self.max_distance_top_k = int(max_distance_top_k)
        self.mean_distance_top_k = int(mean_distance_top_k)
        self.axes_weights = weights

    @property
    def confidence_range(self) -> int:
        return min(self.max_distance_top_k, self.mean_distance_top_k)

    def classify(self, landmarks: Sequence[Sequence[float]]) -> dict[str, int]:
        original = repdetect_pose_embedding(landmarks)
        flipped_landmarks = _landmark_array(landmarks).copy()
        flipped_landmarks[:, 0] *= -1.0
        flipped = repdetect_pose_embedding(flipped_landmarks)

        max_distances: list[tuple[float, str, np.ndarray]] = []
        for class_name, reference in self._samples:
            original_max = float(np.max(np.abs((original - reference) * self.axes_weights)))
            flipped_max = float(np.max(np.abs((flipped - reference) * self.axes_weights)))
            max_distances.append((min(original_max, flipped_max), class_name, reference))
        max_distances.sort(key=lambda item: item[0])

        mean_distances: list[tuple[float, str]] = []
        for _, class_name, reference in max_distances[: self.max_distance_top_k]:
            original_sum = float(np.sum(np.abs((original - reference) * self.axes_weights)))
            flipped_sum = float(np.sum(np.abs((flipped - reference) * self.axes_weights)))
            mean_distance = min(original_sum, flipped_sum) / (original.shape[0] * 2.0)
            mean_distances.append((mean_distance, class_name))
        mean_distances.sort(key=lambda item: item[0])

        votes: dict[str, int] = {}
        for _, class_name in mean_distances[: self.mean_distance_top_k]:
            votes[class_name] = votes.get(class_name, 0) + 1
        return votes


def reproduce_repdetect_landmark_trace(
    class_name: str,
    frames: Iterable[LandmarkPoseFrame],
    classifier: RepDetectKnnClassifier,
    *,
    minimum_visibility: float = 0.70,
) -> RepDetectTraceResult:
    """Run the verified local landmark→KNN→counter pipeline on a trace.

    Images are deliberately outside this contract.  A later Worker may produce
    these local landmarks from a video only after its own privacy and protocol
    checks pass.
    """

    classified: list[ClassifiedPoseFrame] = []
    for frame in frames:
        if frame.framing_complete and frame.tracked_landmark_visibility >= minimum_visibility:
            votes = classifier.classify(frame.landmarks)
            confidence = votes.get(class_name, 0) / classifier.confidence_range
        else:
            confidence = 0.0
        classified.append(
            ClassifiedPoseFrame(
                timestamp_seconds=frame.timestamp_seconds,
                confidence=confidence,
                tracked_landmark_visibility=frame.tracked_landmark_visibility,
                framing_complete=frame.framing_complete,
            )
        )
    return reproduce_repdetect_trace(class_name, classified, minimum_visibility=minimum_visibility)


def reproduce_repdetect_trace(
    class_name: str,
    frames: Iterable[ClassifiedPoseFrame],
    *,
    minimum_visibility: float = 0.70,
) -> RepDetectTraceResult:
    """Run the verified counter only after deterministic capture-quality gates.

    A skipped or bad frame does not get interpolated.  This is intentional: the
    product must ask for a recapture rather than manufacture a motion result.
    """

    if not 0 < minimum_visibility <= 1:
        raise ValueError("minimum_visibility must be within (0, 1]")
    counter = RepDetectRepetitionCounter(class_name)
    accepted = 0
    rejected = 0
    visibility: list[float] = []
    previous_timestamp = float("-inf")

    for frame in frames:
        if not math.isfinite(frame.timestamp_seconds) or frame.timestamp_seconds <= previous_timestamp:
            raise ValueError("frame timestamps must be finite and strictly increasing")
        previous_timestamp = frame.timestamp_seconds
        if (
            not math.isfinite(frame.tracked_landmark_visibility)
            or not 0 <= frame.tracked_landmark_visibility <= 1
            or not math.isfinite(frame.confidence)
            or not 0 <= frame.confidence <= 1
            or not frame.framing_complete
            or frame.tracked_landmark_visibility < minimum_visibility
        ):
            rejected += 1
            continue
        visibility.append(frame.tracked_landmark_visibility)
        counter.observe(frame.confidence)
        accepted += 1

    quality = min(visibility) if visibility else None
    state = "completed" if accepted and not rejected else "needs_review"
    return RepDetectTraceResult(
        class_name=class_name,
        repetitions=counter.num_repeats if state == "completed" else 0,
        accepted_frames=accepted,
        rejected_frames=rejected,
        quality_score=quality,
        state=state,
    )


@dataclass(frozen=True)
class CalibratedObservation:
    """A synchronized 2D marker observation from one calibrated camera."""

    camera_id: str
    projection_matrix: Sequence[Sequence[float]]
    point_xy: Sequence[float]
    timestamp_seconds: float
    confidence: float


@dataclass(frozen=True)
class TriangulationResult:
    point_xyz: tuple[float, float, float] | None
    cameras_used: tuple[str, ...]
    max_sync_delta_seconds: float | None
    mean_reprojection_error_pixels: float | None
    state: str
    reason: str
    non_diagnostic: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _matrix_for(observation: CalibratedObservation) -> np.ndarray:
    matrix = np.asarray(observation.projection_matrix, dtype=float)
    if matrix.shape != (3, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"camera {observation.camera_id!r} requires a finite 3x4 projection matrix")
    return matrix


def project_point(projection_matrix: Sequence[Sequence[float]], point_xyz: Sequence[float]) -> tuple[float, float]:
    """Project one world point through a 3x4 camera matrix."""

    matrix = np.asarray(projection_matrix, dtype=float)
    point = np.asarray((*point_xyz, 1.0), dtype=float)
    if matrix.shape != (3, 4) or point.shape != (4,) or not np.isfinite(matrix).all() or not np.isfinite(point).all():
        raise ValueError("projection requires finite 3x4 matrix and finite xyz point")
    projected = matrix @ point
    if abs(projected[2]) < 1e-12:
        raise ValueError("point projects at infinity")
    return float(projected[0] / projected[2]), float(projected[1] / projected[2])


def triangulate_calibrated_observations(
    observations: Iterable[CalibratedObservation],
    *,
    minimum_confidence: float = 0.70,
    max_sync_delta_seconds: float = 0.03,
    max_mean_reprojection_error_pixels: float = 3.0,
) -> TriangulationResult:
    """Triangulate a single marker by weighted DLT and reject bad geometry.

    The result is a technical reconstruction quality check only.  It must not
    be turned into joint angles or medical interpretation without the complete
    OpenCap/OpenSim worker and its separate validation gate.
    """

    if not 0 < minimum_confidence <= 1:
        raise ValueError("minimum_confidence must be within (0, 1]")
    valid: list[tuple[CalibratedObservation, np.ndarray, float, float]] = []
    seen_ids: set[str] = set()
    for observation in observations:
        if not observation.camera_id or observation.camera_id in seen_ids:
            return TriangulationResult(None, tuple(), None, None, "needs_review", "相机标识缺失或重复。")
        seen_ids.add(observation.camera_id)
        point = np.asarray(observation.point_xy, dtype=float)
        if (
            point.shape != (2,)
            or not np.isfinite(point).all()
            or not math.isfinite(observation.timestamp_seconds)
            or not math.isfinite(observation.confidence)
            or not 0 <= observation.confidence <= 1
        ):
            return TriangulationResult(None, tuple(), None, None, "needs_review", "二维关键点或置信度无效。")
        if observation.confidence >= minimum_confidence:
            valid.append((observation, _matrix_for(observation), float(point[0]), float(point[1])))

    if len(valid) < 2:
        return TriangulationResult(None, tuple(item[0].camera_id for item in valid), None, None, "needs_review", "至少需要两个高质量、已标定的相机观测。")

    timestamps = [item[0].timestamp_seconds for item in valid]
    sync_delta = max(timestamps) - min(timestamps)
    camera_ids = tuple(item[0].camera_id for item in valid)
    if sync_delta > max_sync_delta_seconds:
        return TriangulationResult(None, camera_ids, sync_delta, None, "needs_review", "多机位时间不同步，不能进行三维重建。")

    rows: list[np.ndarray] = []
    for observation, matrix, x, y in valid:
        weight = math.sqrt(observation.confidence)
        rows.extend((weight * (x * matrix[2] - matrix[0]), weight * (y * matrix[2] - matrix[1])))
    design_matrix = np.asarray(rows, dtype=float)
    if np.linalg.matrix_rank(design_matrix) < 3:
        return TriangulationResult(None, camera_ids, sync_delta, None, "needs_review", "标定几何退化，无法稳定三角化。")
    _, _, right_vectors = np.linalg.svd(design_matrix)
    homogeneous = right_vectors[-1]
    if abs(homogeneous[3]) < 1e-10:
        return TriangulationResult(None, camera_ids, sync_delta, None, "needs_review", "标定几何退化，无法稳定三角化。")
    xyz = homogeneous[:3] / homogeneous[3]
    if not np.isfinite(xyz).all():
        return TriangulationResult(None, camera_ids, sync_delta, None, "needs_review", "三角化结果无效。")

    errors: list[float] = []
    for _, matrix, x, y in valid:
        projected_x, projected_y = project_point(matrix, xyz)
        errors.append(math.hypot(projected_x - x, projected_y - y))
    mean_error = sum(errors) / len(errors)
    if mean_error > max_mean_reprojection_error_pixels:
        return TriangulationResult(None, camera_ids, sync_delta, mean_error, "needs_review", "重投影误差过大，需要检查标定或重新采集。")
    return TriangulationResult(
        point_xyz=(float(xyz[0]), float(xyz[1]), float(xyz[2])),
        cameras_used=camera_ids,
        max_sync_delta_seconds=sync_delta,
        mean_reprojection_error_pixels=mean_error,
        state="completed",
        reason="已通过标定、同步和重投影质量门；结果仅供后续受控 Worker 使用。",
    )
