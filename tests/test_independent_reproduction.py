from __future__ import annotations

import math

import pytest

from mindspine.independent_reproduction import (
    CalibratedObservation,
    ClassifiedPoseFrame,
    LandmarkPoseFrame,
    ReferencePoseSample,
    RepDetectRepetitionCounter,
    RepDetectKnnClassifier,
    project_point,
    repdetect_pose_embedding,
    reproduce_repdetect_landmark_trace,
    reproduce_repdetect_trace,
    triangulate_calibrated_observations,
)


def _reference_pose(kind: str) -> list[tuple[float, float, float]]:
    points = [(index * 0.01, (index % 7) * 0.02, (index % 5) * 0.01) for index in range(33)]
    points[11], points[12] = (-0.35, 1.1, 0.02), (0.35, 1.1, 0.02)
    points[13], points[14] = (-0.58, 0.82, 0.03), (0.58, 0.82, 0.03)
    points[15], points[16] = (-0.68, 0.52, 0.04), (0.68, 0.52, 0.04)
    points[23], points[24] = (-0.22, 0.0, 0.0), (0.22, 0.0, 0.0)
    if kind == "squat":
        points[25], points[26] = (-0.58, -0.28, 0.12), (0.58, -0.28, 0.12)
        points[27], points[28] = (-0.24, -0.74, 0.02), (0.24, -0.74, 0.02)
    else:
        points[25], points[26] = (-0.22, -0.55, 0.02), (0.22, -0.55, 0.02)
        points[27], points[28] = (-0.22, -1.05, 0.02), (0.22, -1.05, 0.02)
    return points


def _repdetect_classifier() -> RepDetectKnnClassifier:
    samples = []
    for index in range(12):
        scale = 1.0 + index * 0.002
        for pose, class_name in (("squat", "squats"), ("standing", "standing")):
            sample = [(x * scale + 1.7, y * scale - 0.4, z * scale + 0.1) for x, y, z in _reference_pose(pose)]
            samples.append(ReferencePoseSample(f"{class_name}-{index}", class_name, sample))
    return RepDetectKnnClassifier(samples)


def test_repdetect_counter_preserves_upstream_strict_hysteresis() -> None:
    counter = RepDetectRepetitionCounter("squats")
    for confidence in (0.60, 0.61, 0.40, 0.39, 0.61, 0.39):
        counter.observe(confidence)
    # 0.60 and 0.40 are intentionally not threshold crossings, matching the
    # upstream Java implementation's > enter and < exit comparisons.
    assert counter.num_repeats == 2


def test_repdetect_knn_embedding_is_scale_translation_and_mirror_invariant() -> None:
    classifier = _repdetect_classifier()
    squat = _reference_pose("squat")
    mirrored = [(-x * 1.8 - 2.2, y * 1.8 + 3.0, z * 1.8) for x, y, z in squat]
    assert repdetect_pose_embedding(squat).shape == (23, 3)
    assert classifier.classify(mirrored) == {"squats": 10}


def test_repdetect_landmark_trace_runs_knn_then_counts_completed_cycles() -> None:
    classifier = _repdetect_classifier()
    frames = [
        LandmarkPoseFrame(0.0, _reference_pose("standing"), 0.95),
        LandmarkPoseFrame(0.1, _reference_pose("squat"), 0.95),
        LandmarkPoseFrame(0.2, _reference_pose("standing"), 0.95),
        LandmarkPoseFrame(0.3, _reference_pose("squat"), 0.95),
        LandmarkPoseFrame(0.4, _reference_pose("standing"), 0.95),
    ]
    result = reproduce_repdetect_landmark_trace("squats", frames, classifier)
    assert result.state == "completed"
    assert result.repetitions == 2


def test_repdetect_trace_requires_every_frame_to_pass_capture_quality() -> None:
    trace = [
        ClassifiedPoseFrame(0.0, 0.2, 0.98),
        ClassifiedPoseFrame(0.1, 0.72, 0.96),
        ClassifiedPoseFrame(0.2, 0.32, 0.95),
        ClassifiedPoseFrame(0.3, 0.72, 0.96),
        ClassifiedPoseFrame(0.4, 0.32, 0.95),
    ]
    result = reproduce_repdetect_trace("squats", trace)
    assert result.state == "completed"
    assert result.repetitions == 2
    assert result.quality_score == 0.95

    failed = reproduce_repdetect_trace(
        "squats",
        [*trace, ClassifiedPoseFrame(0.5, 0.4, 0.50)],
    )
    assert failed.state == "needs_review"
    assert failed.repetitions == 0
    assert failed.rejected_frames == 1


def test_repdetect_trace_rejects_non_monotonic_timestamps() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        reproduce_repdetect_trace(
            "squats",
            [ClassifiedPoseFrame(1.0, 0.8, 0.9), ClassifiedPoseFrame(1.0, 0.3, 0.9)],
        )


def test_calibrated_dlt_triangulation_reconstructs_synthetic_marker() -> None:
    # Two simple calibrated pinhole cameras in a common metric coordinate frame.
    camera_a = ((900.0, 0.0, 320.0, 0.0), (0.0, 900.0, 240.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    camera_b = ((900.0, 0.0, 320.0, -450.0), (0.0, 900.0, 240.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    source = (0.15, -0.05, 3.20)
    result = triangulate_calibrated_observations(
        [
            CalibratedObservation("cam-a", camera_a, project_point(camera_a, source), 10.000, 0.98),
            CalibratedObservation("cam-b", camera_b, project_point(camera_b, source), 10.012, 0.97),
        ]
    )
    assert result.state == "completed"
    assert result.point_xyz is not None
    assert result.mean_reprojection_error_pixels is not None
    assert result.mean_reprojection_error_pixels < 1e-7
    assert all(math.isclose(actual, expected, abs_tol=1e-7) for actual, expected in zip(result.point_xyz, source))


def test_triangulation_rejects_unsynchronized_or_low_confidence_observations() -> None:
    camera = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    unsynchronized = triangulate_calibrated_observations(
        [
            CalibratedObservation("cam-a", camera, (0.1, 0.1), 0.0, 0.9),
            CalibratedObservation("cam-b", camera, (0.2, 0.1), 0.2, 0.9),
        ]
    )
    assert unsynchronized.state == "needs_review"
    assert "不同步" in unsynchronized.reason

    low_quality = triangulate_calibrated_observations(
        [
            CalibratedObservation("cam-a", camera, (0.1, 0.1), 0.0, 0.4),
            CalibratedObservation("cam-b", camera, (0.2, 0.1), 0.0, 0.9),
        ]
    )
    assert low_quality.state == "needs_review"
    assert "至少需要两个" in low_quality.reason


def test_triangulation_rejects_degenerate_same_camera_geometry() -> None:
    camera = ((900.0, 0.0, 320.0, 0.0), (0.0, 900.0, 240.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    result = triangulate_calibrated_observations(
        [
            CalibratedObservation("cam-a", camera, (330.0, 250.0), 0.0, 0.9),
            CalibratedObservation("cam-b", camera, (330.0, 250.0), 0.0, 0.9),
        ]
    )
    assert result.state == "needs_review"
    assert "退化" in result.reason
