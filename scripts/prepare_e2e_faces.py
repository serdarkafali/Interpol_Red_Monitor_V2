"""Pick synthetic faces for the face worker's end-to-end run (ADR 0012).

Every face comes from DigiFace-1M, a synthetic dataset: no real person is used. The
script reads the dataset zip in place, runs the face worker's own FaceAnalyzer on the
images and writes five JPEGs, named after the made-up pictures of
scripts/produce_fake_events.py, which the fake Interpol server then serves:

- 1900-7-photo-1, 1900-7-photo-2: person A (two photos of one notice that match)
- 1900-8-photo:                   person A again (a possible match with another notice)
- 1900-9-photo-1:                 person B
- 1900-9-photo-2:                 person C (a photo that does not match its own notice)

Only images on which exactly one face is found are used, and the people are chosen so
that the distances are clearly on the expected side of the threshold. DigiFace crops are
112x112 and fill the image, so each one is padded with a grey border first.

Everything stays under samples/, which is never committed (DigiFace's licence allows
non-commercial research use only; the images are not redistributed). Run from the
repository root:
    uv run python scripts/prepare_e2e_faces.py \
        --dataset samples/faces/subjects_133333-166665_5_imgs.zip \
        --models samples/models --output samples/e2e-faces
"""

from __future__ import annotations

import argparse
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

import cv2
import numpy as np

from interpol_face_worker.face_analysis import FaceAnalyzer
from interpol_face_worker.repository import MAX_MATCH_DISTANCE

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"
# Margins around the threshold, so that the run does not depend on a borderline pair.
SAME_PERSON_MAX = MAX_MATCH_DISTANCE - 0.1
OTHER_PERSON_MIN = MAX_MATCH_DISTANCE + 0.15
BORDER = 56
SUBJECTS_TO_TRY = 200


def padded_jpeg(png: bytes) -> bytes | None:
    """The DigiFace crop with a grey border, as JPEG; None if it cannot be read."""
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    image = cv2.copyMakeBorder(
        image, BORDER, BORDER, BORDER, BORDER, cv2.BORDER_CONSTANT, value=(128, 128, 128)
    )
    ok, encoded = cv2.imencode(".jpg", image)
    return encoded.tobytes() if ok else None


def distance(first: list[float], second: list[float]) -> float:
    """Cosine distance of two L2-normalised embeddings."""
    return 1.0 - float(np.dot(first, second))


def faces_by_subject(
    dataset: Path, analyzer: FaceAnalyzer
) -> dict[str, list[tuple[bytes, list[float]]]]:
    """For each subject, its images with exactly one face: (JPEG, embedding)."""
    found: dict[str, list[tuple[bytes, list[float]]]] = defaultdict(list)
    with zipfile.ZipFile(dataset) as archive:
        names = sorted(name for name in archive.namelist() if name.endswith(".png"))
        subjects: list[str] = []
        for name in names:
            subject = PurePosixPath(name).parent.name
            if subject not in subjects:
                if len(subjects) == SUBJECTS_TO_TRY:
                    break
                subjects.append(subject)
            jpeg = padded_jpeg(archive.read(name))
            if jpeg is None:
                continue
            faces = analyzer.analyze(jpeg)
            if len(faces) == 1:
                found[subject].append((jpeg, faces[0].embedding))
    return found


def pick(
    found: dict[str, list[tuple[bytes, list[float]]]],
) -> dict[str, tuple[bytes, list[float]]]:
    """Choose person A (three matching images) and people B and C (one image each)."""
    for subject_a, images in found.items():
        if len(images) < 3:
            continue
        trio = images[:3]
        if any(
            distance(trio[i][1], trio[j][1]) >= SAME_PERSON_MAX
            for i in range(3)
            for j in range(i + 1, 3)
        ):
            continue
        others: list[tuple[bytes, list[float]]] = []
        for subject, other_images in found.items():
            if subject == subject_a:
                continue
            candidate = other_images[0]
            chosen = [*trio, *others]
            if all(distance(candidate[1], face[1]) > OTHER_PERSON_MIN for face in chosen):
                others.append(candidate)
            if len(others) == 2:
                return {
                    "1900-7-photo-1": trio[0],
                    "1900-7-photo-2": trio[1],
                    "1900-8-photo": trio[2],
                    "1900-9-photo-1": others[0],
                    "1900-9-photo-2": others[1],
                }
    raise SystemExit("No suitable faces found; try another dataset zip.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dataset", type=Path, required=True, help="DigiFace-1M zip")
    parser.add_argument("--models", type=Path, required=True, help="folder of the ONNX models")
    parser.add_argument("--output", type=Path, default=Path("samples/e2e-faces"))
    args = parser.parse_args()

    analyzer = FaceAnalyzer.load(args.models / DETECTOR_FILE, args.models / RECOGNIZER_FILE)
    chosen = pick(faces_by_subject(args.dataset, analyzer))
    args.output.mkdir(parents=True, exist_ok=True)
    for picture_id, (jpeg, _) in chosen.items():
        (args.output / f"{picture_id}.jpg").write_bytes(jpeg)

    embeddings = {picture_id: embedding for picture_id, (_, embedding) in chosen.items()}
    print(f"{len(chosen)} synthetic faces written to {args.output}")
    for first, second in [
        ("1900-7-photo-1", "1900-7-photo-2"),
        ("1900-7-photo-1", "1900-8-photo"),
        ("1900-9-photo-1", "1900-9-photo-2"),
        ("1900-7-photo-1", "1900-9-photo-1"),
    ]:
        print(f"  {first} - {second}: {distance(embeddings[first], embeddings[second]):.3f}")


if __name__ == "__main__":
    main()
