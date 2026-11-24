"""Compare face embedding models on synthetic faces before choosing one (ADR 0012).

Every face comes from a synthetic dataset (DigiFace-1M): no real person is used. The
dataset zip is read in place; nothing is extracted and nothing is written.

For each model it measures how close the embeddings of the same identity are (genuine
pairs) and how far apart different identities are (impostor pairs), the threshold that
separates them best, the error rates at that threshold and the time per face. It also
measures how often the YuNet detector finds the face at all.

DigiFace images are already cropped and aligned to 112x112, so the embedders receive them
as they are. For the detection rate the crop is padded with a border, so that the face
does not fill the whole image.

Everything goes under samples/, which is never committed:
- samples/faces/: one DigiFace-1M "5 images per identity" zip, e.g.
  subjects_100000-133332_5_imgs.zip (3.4 GB; github.com/microsoft/DigiFace1M).
  Non-commercial research licence; it is only read here, never redistributed.
- samples/models/: face_detection_yunet_2023mar.onnx (MIT) and
  face_recognition_sface_2021dec.onnx (Apache-2.0) from github.com/opencv/opencv_zoo;
  glintr100.onnx (Apache-2.0) from huggingface.co/fal/AuraFace-v1; optionally
  w600k_r50.onnx from InsightFace's buffalo_l pack, as a reference only (its licence
  allows non-commercial research use, so it cannot be a candidate).

Run from the repository root:
    uv run python scripts/compare_face_models.py \
        --dataset samples/faces/subjects_100000-133332_5_imgs.zip \
        --yunet samples/models/face_detection_yunet_2023mar.onnx \
        --sface samples/models/face_recognition_sface_2021dec.onnx \
        --auraface samples/models/glintr100.onnx \
        --insightface samples/models/w600k_r50.onnx
"""

from __future__ import annotations

import argparse
import itertools
import random
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
import numpy.typing as npt
import onnxruntime as ort

Image = npt.NDArray[np.uint8]
Vector = npt.NDArray[np.float32]
Embedder = Callable[[Image], Vector]

FACE_SIZE = 112
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
# Padding around the 112x112 crop for the detector, so the face is about half the image.
DETECTION_PADDING = 56
DETECTION_SCORE_THRESHOLD = 0.6


@dataclass(frozen=True)
class Sample:
    """One synthetic face and the identity it belongs to."""

    identity: str
    image: Image


@dataclass(frozen=True)
class Result:
    """What one model achieved on the sample."""

    name: str
    dimensions: int
    ms_per_face: float
    genuine_median: float
    impostor_median: float
    eer: float
    eer_threshold: float
    threshold_at_far_1pct: float
    frr_at_far_1pct: float


def load_samples(dataset: Path, identities: int, seed: int) -> list[Sample]:
    """Read the images of ``identities`` randomly chosen identities from the zip.

    The identity of an image is the folder it is in. Identities with fewer than two
    images are skipped: they cannot form a genuine pair.
    """
    with zipfile.ZipFile(dataset) as archive:
        names_by_identity: dict[str, list[str]] = defaultdict(list)
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.suffix.lower() in IMAGE_SUFFIXES and len(path.parts) >= 2:
                names_by_identity[path.parts[-2]].append(name)
        eligible = sorted(i for i, names in names_by_identity.items() if len(names) >= 2)
        chosen = random.Random(seed).sample(eligible, min(identities, len(eligible)))

        samples: list[Sample] = []
        for identity in chosen:
            for name in sorted(names_by_identity[identity]):
                data = np.frombuffer(archive.read(name), dtype=np.uint8)
                image = cv2.imdecode(data, cv2.IMREAD_COLOR)
                if image is not None:
                    face = np.asarray(cv2.resize(image, (FACE_SIZE, FACE_SIZE)), dtype=np.uint8)
                    samples.append(Sample(identity=identity, image=face))
    return samples


def make_pairs(
    samples: list[Sample], seed: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Index pairs of the same identity, and as many random pairs of different ones."""
    indexes_by_identity: dict[str, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        indexes_by_identity[sample.identity].append(index)
    genuine = [
        pair
        for indexes in indexes_by_identity.values()
        for pair in itertools.combinations(indexes, 2)
    ]

    rng = random.Random(seed)
    impostor: set[tuple[int, int]] = set()
    while len(impostor) < len(genuine):
        a, b = sorted(rng.sample(range(len(samples)), 2))
        if samples[a].identity != samples[b].identity:
            impostor.add((a, b))
    return genuine, sorted(impostor)


def normalize(vector: npt.ArrayLike) -> Vector:
    """Scale to length 1, so that a dot product is the cosine similarity."""
    flat = np.asarray(vector, dtype=np.float32).flatten()
    norm = float(np.linalg.norm(flat))
    return flat / norm if norm > 0 else flat


def sface_embedder(model: Path) -> Embedder:
    """SFace through OpenCV; it expects a 112x112 BGR crop."""
    recognizer = cv2.FaceRecognizerSF.create(str(model), "")

    def embed(face: Image) -> Vector:
        return normalize(recognizer.feature(face))

    return embed


def arcface_onnx_embedder(model: Path) -> Embedder:
    """An ArcFace-style ONNX model (AuraFace, InsightFace): RGB, scaled to [-1, 1]."""
    options = ort.SessionOptions()
    options.log_severity_level = 3  # errors only; some exports print a warning per weight
    session = ort.InferenceSession(
        str(model), sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_name = session.get_inputs()[0].name

    def embed(face: Image) -> Vector:
        blob = cv2.dnn.blobFromImage(
            face, 1.0 / 127.5, (FACE_SIZE, FACE_SIZE), (127.5, 127.5, 127.5), swapRB=True
        )
        outputs = session.run(None, {input_name: blob})
        return normalize(np.asarray(outputs[0], dtype=np.float32))

    return embed


def detection_rate(model: Path, samples: list[Sample]) -> float:
    """Share of samples in which YuNet finds at least one face."""
    size = FACE_SIZE + 2 * DETECTION_PADDING
    detector = cv2.FaceDetectorYN.create(
        str(model), "", (size, size), score_threshold=DETECTION_SCORE_THRESHOLD
    )
    found = 0
    for sample in samples:
        padded = cv2.copyMakeBorder(
            sample.image,
            DETECTION_PADDING,
            DETECTION_PADDING,
            DETECTION_PADDING,
            DETECTION_PADDING,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        _, faces = detector.detect(padded)
        if faces is not None and len(faces) > 0:
            found += 1
    return found / len(samples)


def evaluate(
    name: str,
    embedder: Embedder,
    samples: list[Sample],
    genuine: list[tuple[int, int]],
    impostor: list[tuple[int, int]],
) -> Result:
    """Embed every sample once and measure how well the similarities separate the pairs."""
    started = time.perf_counter()
    vectors = [embedder(sample.image) for sample in samples]
    ms_per_face = (time.perf_counter() - started) * 1000 / len(samples)

    genuine_scores = np.array([float(vectors[a] @ vectors[b]) for a, b in genuine])
    impostor_scores = np.array([float(vectors[a] @ vectors[b]) for a, b in impostor])

    thresholds = np.linspace(-1.0, 1.0, 2001)
    # FAR: different people called the same; FRR: the same person missed.
    far = np.array([np.mean(impostor_scores >= t) for t in thresholds])
    frr = np.array([np.mean(genuine_scores < t) for t in thresholds])

    eer_index = int(np.argmin(np.abs(far - frr)))
    # The lowest threshold that keeps FAR at or below 1%.
    far_1pct_index = int(np.argmax(far <= 0.01))

    return Result(
        name=name,
        dimensions=len(vectors[0]),
        ms_per_face=ms_per_face,
        genuine_median=float(np.median(genuine_scores)),
        impostor_median=float(np.median(impostor_scores)),
        eer=float((far[eer_index] + frr[eer_index]) / 2),
        eer_threshold=float(thresholds[eer_index]),
        threshold_at_far_1pct=float(thresholds[far_1pct_index]),
        frr_at_far_1pct=float(frr[far_1pct_index]),
    )


def print_results(results: list[Result]) -> None:
    header = (
        f"{'model':<14} {'dim':>4} {'ms/face':>8} {'genuine':>8} {'impostor':>9} "
        f"{'EER':>7} {'EER thr':>8} {'thr@FAR1%':>10} {'FRR@FAR1%':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.name:<14} {r.dimensions:>4} {r.ms_per_face:>8.1f} {r.genuine_median:>8.3f} "
            f"{r.impostor_median:>9.3f} {r.eer:>7.2%} {r.eer_threshold:>8.3f} "
            f"{r.threshold_at_far_1pct:>10.3f} {r.frr_at_far_1pct:>10.2%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, required=True, help="DigiFace-1M zip")
    parser.add_argument("--identities", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--yunet", type=Path, help="YuNet ONNX file (detection rate)")
    parser.add_argument("--sface", type=Path, help="SFace ONNX file")
    parser.add_argument("--auraface", type=Path, help="AuraFace glintr100.onnx")
    parser.add_argument(
        "--insightface", type=Path, help="InsightFace w600k_r50.onnx (reference only)"
    )
    args = parser.parse_args()

    samples = load_samples(args.dataset, args.identities, args.seed)
    genuine, impostor = make_pairs(samples, args.seed)
    identities = len({s.identity for s in samples})
    print(f"{len(samples)} faces of {identities} synthetic identities")
    print(f"{len(genuine)} genuine pairs, {len(impostor)} impostor pairs\n")

    if args.yunet:
        print(f"YuNet detection rate: {detection_rate(args.yunet, samples):.1%}\n")

    candidates: list[tuple[str, Embedder]] = []
    if args.sface:
        candidates.append(("SFace", sface_embedder(args.sface)))
    if args.auraface:
        candidates.append(("AuraFace", arcface_onnx_embedder(args.auraface)))
    if args.insightface:
        candidates.append(("InsightFace*", arcface_onnx_embedder(args.insightface)))
    if not candidates:
        parser.error("give at least one model: --sface, --auraface or --insightface")

    print_results([evaluate(name, e, samples, genuine, impostor) for name, e in candidates])
    if args.insightface:
        print("\n* reference only: non-commercial research licence, not a candidate")


if __name__ == "__main__":
    main()
