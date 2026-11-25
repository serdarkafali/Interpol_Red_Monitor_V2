"""Face detection (YuNet) and face embeddings (SFace) for a single photo.

This module only works on image bytes: it knows nothing about the database or the
object store. See ADR 0012 for why these models were chosen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Self

import cv2
import numpy as np

# Stored in faces.model so embeddings from different models are never compared.
MODEL_NAME = "sface-2021dec"

# YuNet drops faces whose confidence is below this value (ADR 0012).
MIN_DETECTION_SCORE = 0.6
# Faces whose shorter box side is below this many pixels are skipped: the embeddings of
# tiny, blurry faces produce false matches (ADR 0012).
MIN_FACE_SIZE = 40

# Only a starting value: analyze() sets the real size of every photo.
_INITIAL_INPUT_SIZE = (320, 320)

# Column of the confidence score in one YuNet result row
# (0-3: box, 4-13: five landmarks, 14: score).
_SCORE_COLUMN = 14


class FaceAnalysisError(Exception):
    """The photo could not be analysed; ``reason`` is a short fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DetectedFace:
    """One face found on a photo, with the same fields as the faces table."""

    box_x: int
    box_y: int
    box_width: int
    box_height: int
    detection_score: float
    # L2-normalised, so cosine similarity is a plain dot product.
    embedding: list[float]


class FaceAnalyzer:
    """Finds the faces on a photo and computes an embedding for each of them."""

    def __init__(self, detector: cv2.FaceDetectorYN, recognizer: cv2.FaceRecognizerSF) -> None:
        self._detector = detector
        self._recognizer = recognizer

    @classmethod
    def load(cls, detector_path: Path, recognizer_path: Path) -> Self:
        """Load both ONNX models from disk."""
        detector = cv2.FaceDetectorYN.create(
            str(detector_path), "", _INITIAL_INPUT_SIZE, MIN_DETECTION_SCORE
        )
        recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        return cls(detector, recognizer)

    def analyze(self, jpeg: bytes) -> list[DetectedFace]:
        """Return every face on the photo; an empty list if there is none."""
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise FaceAnalysisError("not_decodable")

        height, width = image.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(image)
        if faces is None:
            return []

        detected: list[DetectedFace] = []
        for row in faces:
            box_x, box_y, box_width, box_height = (int(value) for value in row[:4])
            if min(box_width, box_height) < MIN_FACE_SIZE:
                continue
            aligned = self._recognizer.alignCrop(image, row)
            feature = self._recognizer.feature(aligned)
            detected.append(
                DetectedFace(
                    box_x=box_x,
                    box_y=box_y,
                    box_width=box_width,
                    box_height=box_height,
                    detection_score=float(row[_SCORE_COLUMN]),
                    embedding=self._normalize(feature),
                )
            )
        return detected

    @staticmethod
    def _normalize(feature: np.ndarray) -> list[float]:
        """Scale the embedding to length 1 and turn it into a Python list."""
        vector = feature.flatten().astype(np.float64)
        normalized: list[float] = (vector / np.linalg.norm(vector)).tolist()
        return normalized
