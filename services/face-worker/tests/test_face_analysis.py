"""Tests of FaceAnalyzer with fake OpenCV models, plus a smoke test with the real ones.

The fakes check how analyze() uses YuNet and SFace results without the 38 MB model
files. The real models are only used when FACE_MODELS_DIR points to a folder that
holds them, so CI does not need to download them.
"""

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from interpol_face_worker.face_analysis import (
    MIN_FACE_SIZE,
    MODEL_NAME,
    DetectedFace,
    FaceAnalysisError,
    FaceAnalyzer,
)

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"


def jpeg(width: int, height: int) -> bytes:
    """A plain grey made-up photo of the given size, encoded as JPEG."""
    image = np.full((height, width, 3), 128, np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def yunet_row(x: float, y: float, width: float, height: float, score: float) -> list[float]:
    """One YuNet result row: box, five made-up landmarks and the score."""
    return [x, y, width, height, *([0.0] * 10), score]


class FakeDetector:
    def __init__(self, rows: list[list[float]] | None) -> None:
        self.faces = None if rows is None else np.array(rows, np.float32)
        self.input_sizes: list[tuple[int, int]] = []

    def setInputSize(self, size: tuple[int, int]) -> None:  # noqa: N802 - OpenCV's name
        self.input_sizes.append(size)

    def detect(self, image: np.ndarray) -> tuple[int, np.ndarray | None]:
        return 1, self.faces


class FakeRecognizer:
    """Returns the embedding [3, 4, 0, ...] for the first face, [0, 0, 5, ...] after."""

    def __init__(self) -> None:
        self.aligned_rows: list[list[float]] = []

    def alignCrop(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:  # noqa: N802
        self.aligned_rows.append(row.tolist())
        return np.zeros((112, 112, 3), np.uint8)

    def feature(self, aligned: np.ndarray) -> np.ndarray:
        vector = np.zeros((1, 128), np.float32)
        if len(self.aligned_rows) == 1:
            vector[0, :2] = [3.0, 4.0]
        else:
            vector[0, 2] = 5.0
        return vector


def analyzer(detector: Any, recognizer: Any = None) -> FaceAnalyzer:
    """A FaceAnalyzer on fakes; Any because the fakes are not OpenCV classes."""
    chosen: Any = recognizer if recognizer is not None else FakeRecognizer()
    return FaceAnalyzer(detector, chosen)


def test_bytes_that_are_not_an_image_raise_not_decodable() -> None:
    with pytest.raises(FaceAnalysisError) as error:
        analyzer(FakeDetector(None)).analyze(b"not a photo")

    assert error.value.reason == "not_decodable"


def test_photo_without_faces_gives_an_empty_list() -> None:
    assert analyzer(FakeDetector(None)).analyze(jpeg(64, 48)) == []


def test_detector_gets_the_size_of_each_photo_as_width_and_height() -> None:
    detector = FakeDetector(None)
    face_analyzer = analyzer(detector)

    face_analyzer.analyze(jpeg(64, 48))
    face_analyzer.analyze(jpeg(32, 80))

    assert detector.input_sizes == [(64, 48), (32, 80)]


def test_one_face_gives_its_box_score_and_normalised_embedding() -> None:
    detector = FakeDetector([yunet_row(80.6, 66.2, 75.9, 100.0, 0.917)])

    [face] = analyzer(detector).analyze(jpeg(224, 224))

    assert (face.box_x, face.box_y, face.box_width, face.box_height) == (80, 66, 75, 100)
    assert face.detection_score == pytest.approx(0.917)
    assert len(face.embedding) == 128
    # [3, 4] has length 5, so normalising gives [0.6, 0.8].
    assert face.embedding[:2] == pytest.approx([0.6, 0.8])
    assert np.linalg.norm(face.embedding) == pytest.approx(1.0)
    assert all(isinstance(value, float) for value in face.embedding)


def test_every_face_on_the_photo_is_returned_in_detector_order() -> None:
    rows = [yunet_row(10, 20, 60, 80, 0.95), yunet_row(90, 60, 50, 65, 0.91)]
    recognizer = FakeRecognizer()

    faces = analyzer(FakeDetector(rows), recognizer).analyze(jpeg(224, 224))

    assert [face.box_x for face in faces] == [10, 90]
    assert faces[1].embedding[:3] == pytest.approx([0.0, 0.0, 1.0])
    # SFace aligns each face with its own YuNet row, landmarks included.
    assert np.allclose(recognizer.aligned_rows, rows)


def test_faces_smaller_than_the_minimum_size_are_skipped() -> None:
    small = MIN_FACE_SIZE - 1
    rows = [
        yunet_row(0, 0, small, 100, 0.95),  # too narrow
        yunet_row(0, 0, 100, small, 0.95),  # too short
        yunet_row(5, 5, MIN_FACE_SIZE, MIN_FACE_SIZE, 0.95),  # just large enough
    ]
    recognizer = FakeRecognizer()

    faces = analyzer(FakeDetector(rows), recognizer).analyze(jpeg(224, 224))

    assert [face.box_x for face in faces] == [5]
    # Skipped faces are never aligned or embedded.
    assert len(recognizer.aligned_rows) == 1


def test_detected_face_cannot_be_changed() -> None:
    face = DetectedFace(1, 2, 3, 4, 0.9, [1.0])

    with pytest.raises(AttributeError):
        face.box_x = 5  # type: ignore[misc]


def test_model_name_matches_the_recognizer_file() -> None:
    assert MODEL_NAME.removeprefix("sface-") in RECOGNIZER_FILE


models_dir = os.environ.get("FACE_MODELS_DIR")


@pytest.mark.skipif(not models_dir, reason="FACE_MODELS_DIR is not set")
def test_real_models_load_and_find_no_face_on_a_blank_photo() -> None:
    folder = Path(models_dir or "")
    face_analyzer = FaceAnalyzer.load(folder / DETECTOR_FILE, folder / RECOGNIZER_FILE)

    assert face_analyzer.analyze(jpeg(320, 240)) == []
