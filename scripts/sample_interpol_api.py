"""Download a small sample of raw Interpol red notice responses for schema analysis.

For each nationality in ``NATIONALITIES`` the script fetches one list page, then the
detail and image-list responses of the first ``NOTICES_PER_LIST`` notices on it.
Responses are stored unchanged as JSON under ``samples/`` (git-ignored, because they
contain personal data). Files that already exist are skipped, so the script can be
re-run after an interruption without repeating requests.

Run from anywhere:
    uv run python scripts/sample_interpol_api.py
"""

import json
import random
import time
from pathlib import Path
from typing import Any

import curl_cffi

from interpol_observability import configure_logging, get_logger

BASE_URL = "https://ws-public.interpol.int/notices/v1/red"
NATIONALITIES = ["TR", "UY", "RU", "US", "DE"]
NOTICES_PER_LIST = 6
RESULTS_PER_PAGE = 20

REQUEST_TIMEOUT_SECONDS = 100
REQUEST_DELAY_MIN_SECONDS = 5.0
REQUEST_DELAY_MAX_SECONDS = 8.0
IMPERSONATE = "chrome120"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.interpol.int/",
    "Origin": "https://www.interpol.int",
}

# Anchored to the repository root, independent of the current working directory.
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

logger = get_logger(__name__)


def fetch_json(url: str, params: dict[str, str | int] | None = None) -> dict[str, Any] | None:
    """GET ``url`` and return the parsed JSON body, or ``None`` on any failure.

    Always waits a random 5-8 seconds afterwards, also after a failure, so that the
    script never sends requests in quick succession.
    """
    try:
        response = curl_cffi.get(
            url,
            params=params,
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning("request_failed", url=url, status_code=response.status_code)
            return None
        payload: dict[str, Any] = response.json()  # type: ignore[no-untyped-call]
        return payload
    except Exception:
        logger.exception("request_error", url=url)
        return None
    finally:
        time.sleep(random.uniform(REQUEST_DELAY_MIN_SECONDS, REQUEST_DELAY_MAX_SECONDS))


def ensure_dir(path: Path) -> None:
    """Create ``path`` and any missing parents; do nothing if it already exists."""
    path.mkdir(parents=True, exist_ok=True)


def save_json(data: dict[str, Any], path: Path) -> None:
    """Write ``data`` to ``path`` as indented UTF-8 JSON, creating its folder."""
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def to_url_id(entity_id: str) -> str:
    """Convert an entity id such as ``2026/72769`` to its URL form ``2026-72769``."""
    return entity_id.replace("/", "-")


def fetch_and_save(url: str, path: Path, params: dict[str, str | int] | None = None) -> bool:
    """Fetch ``url`` into ``path`` unless the file exists; return whether it is on disk."""
    file_label = f"{path.parent.name}/{path.name}"
    if path.exists():
        logger.info("sample_skipped_existing", file=file_label)
        return True
    data = fetch_json(url, params)
    if data is None:
        return False
    save_json(data, path)
    logger.info("sample_saved", file=file_label)
    return True


def collect_notice(entity_id: str) -> None:
    """Save the detail and the image-list responses of one notice."""
    url_id = to_url_id(entity_id)
    fetch_and_save(f"{BASE_URL}/{url_id}", SAMPLES_DIR / "notices" / f"{url_id}.json")
    fetch_and_save(f"{BASE_URL}/{url_id}/images", SAMPLES_DIR / "images" / f"{url_id}.json")


def main() -> None:
    """Collect list, detail and image-list samples for every nationality."""
    configure_logging(service_name="api-sampler", log_format="console")
    logger.info("sampling_started", samples_dir=str(SAMPLES_DIR))

    for nationality in NATIONALITIES:
        list_path = SAMPLES_DIR / "lists" / f"{nationality}.json"
        params: dict[str, str | int] = {
            "nationality": nationality,
            "resultPerPage": RESULTS_PER_PAGE,
            "page": 1,
        }
        if not fetch_and_save(BASE_URL, list_path, params):
            continue

        with list_path.open(encoding="utf-8") as file:
            listing: dict[str, Any] = json.load(file)
        notices: list[dict[str, Any]] = listing.get("_embedded", {}).get("notices", [])
        logger.info(
            "list_loaded",
            nationality=nationality,
            total=listing.get("total"),
            returned=len(notices),
        )

        for notice in notices[:NOTICES_PER_LIST]:
            entity_id = notice.get("entity_id")
            if not isinstance(entity_id, str):
                logger.warning("notice_without_entity_id", nationality=nationality)
                continue
            collect_notice(entity_id)

    logger.info(
        "sampling_finished",
        lists=len(list((SAMPLES_DIR / "lists").glob("*.json"))),
        notices=len(list((SAMPLES_DIR / "notices").glob("*.json"))),
        images=len(list((SAMPLES_DIR / "images").glob("*.json"))),
    )


if __name__ == "__main__":
    main()
