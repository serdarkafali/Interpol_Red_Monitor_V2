"""Summarise the raw Interpol samples collected by ``sample_interpol_api.py``.

Answers the schema questions with counts instead of guesses: which fields are null,
which formats dates use, how often lists hold more than one item, which code values
appear. Only aggregates and code values are printed; names, free text and other
personal data never reach the output.

Run from anywhere (after the sampler):
    uv run python scripts/analyze_samples.py
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

# Fields whose values are personal free text: only their presence and length are reported.
FREE_TEXT_FIELDS = {"name", "forename", "place_of_birth", "distinguishing_marks"}
# Fields whose values are short codes: their distinct values are safe to print.
CODE_FIELDS = {
    "sex_id",
    "country_of_birth_id",
    "nationalities",
    "languages_spoken_ids",
    "eyes_colors_id",
    "hairs_id",
}


def load_all(folder: Path) -> list[dict[str, Any]]:
    """Load every JSON file in ``folder``; return an empty list if it does not exist."""
    documents: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            documents.append(json.load(file))
    return documents


def value_kind(value: Any) -> str:
    """Describe a JSON value for the null/empty report."""
    if value is None:
        return "null"
    if isinstance(value, list):
        return "empty list" if not value else f"list[{type(value[0]).__name__}]"
    if isinstance(value, str) and not value.strip():
        return "empty string"
    return type(value).__name__


def shape_of(text: str) -> str:
    """Replace digits with 9 so that ``1973/06/26`` becomes ``9999/99/99``."""
    return re.sub(r"\d", "9", text)


def print_counter(title: str, counter: Counter[str], limit: int = 15) -> None:
    """Print a counter as ``value: count`` lines, most common first."""
    print(f"  {title}")
    for value, count in counter.most_common(limit):
        print(f"    {value!s:<28} {count}")
    if len(counter) > limit:
        print(f"    ... {len(counter) - limit} more distinct values")


def count_lengths(items: Iterable[Any]) -> Counter[str]:
    """Count how many lists have 0, 1, 2, ... elements (``None`` counted as 'null')."""
    return Counter("null" if item is None else str(len(item)) for item in items)


def analyze_lists(lists: list[dict[str, Any]]) -> None:
    """Report the shape of the list endpoint responses."""
    print(f"\n=== LIST RESPONSES ({len(lists)} files) ===")
    item_keys: Counter[str] = Counter()
    for listing in lists:
        notices = listing.get("_embedded", {}).get("notices", [])
        query = listing.get("query", {})
        print(
            f"  nationality={query.get('nationality')} total={listing.get('total')} "
            f"returned={len(notices)} resultPerPage={query.get('resultPerPage')}"
        )
        for notice in notices:
            item_keys.update(notice.keys())
    print_counter("keys of a list item (how many items have each key):", item_keys)


def analyze_notices(notices: list[dict[str, Any]]) -> None:
    """Report field presence, value kinds, formats and code values of detail responses."""
    total = len(notices)
    print(f"\n=== DETAIL RESPONSES ({total} files) ===")

    all_keys = sorted({key for notice in notices for key in notice})
    print("  field                          missing  " + "kinds (value kind: count)")
    for key in all_keys:
        missing = sum(1 for notice in notices if key not in notice)
        kinds = Counter(value_kind(notice[key]) for notice in notices if key in notice)
        kinds_text = ", ".join(f"{kind}: {count}" for kind, count in kinds.most_common())
        print(f"  {key:<30} {missing:>7}  {kinds_text}")

    dates = [n.get("date_of_birth") for n in notices]
    print_counter(
        "date_of_birth formats:",
        Counter(shape_of(d) if isinstance(d, str) else value_kind(d) for d in dates),
    )

    for field in sorted(CODE_FIELDS):
        values: Counter[str] = Counter()
        for notice in notices:
            value = notice.get(field)
            if isinstance(value, list):
                values.update(str(item) for item in value)
            else:
                values[str(value)] += 1
        print_counter(f"{field} values:", values)

    for field in ("nationalities", "languages_spoken_ids", "eyes_colors_id", "hairs_id"):
        print_counter(f"{field}: items per notice", count_lengths(n.get(field) for n in notices))

    for field in ("height", "weight"):
        numbers = [n[field] for n in notices if isinstance(n.get(field), int | float)]
        kinds = Counter(type(n).__name__ for n in numbers)
        if numbers:
            print(
                f"  {field}: {len(numbers)} numeric, min={min(numbers)}, max={max(numbers)}, "
                f"types={dict(kinds)}"
            )
        else:
            print(f"  {field}: no numeric values")

    for field in sorted(FREE_TEXT_FIELDS):
        lengths = [len(n[field]) for n in notices if isinstance(n.get(field), str)]
        if lengths:
            print(f"  {field}: {len(lengths)} filled, length min={min(lengths)} max={max(lengths)}")

    analyze_warrants(notices)


def analyze_warrants(notices: list[dict[str, Any]]) -> None:
    """Report how many warrants a notice has and which warrant fields are filled."""
    warrants_per_notice = count_lengths(n.get("arrest_warrants") for n in notices)
    print_counter("arrest_warrants: warrants per notice", warrants_per_notice)

    warrants = [w for n in notices for w in (n.get("arrest_warrants") or [])]
    field_kinds: dict[str, Counter[str]] = {}
    for warrant in warrants:
        for key, value in warrant.items():
            field_kinds.setdefault(key, Counter())[value_kind(value)] += 1
    print(f"  arrest_warrant fields ({len(warrants)} warrants):")
    for key, kinds in sorted(field_kinds.items()):
        print(f"    {key:<28} {dict(kinds)}")
    print_counter(
        "issuing_country_id values:",
        Counter(str(w.get("issuing_country_id")) for w in warrants),
    )
    same_as_nationality = sum(
        1
        for n in notices
        for w in (n.get("arrest_warrants") or [])
        if w.get("issuing_country_id") in (n.get("nationalities") or [])
    )
    print(f"  warrants issued by one of the person's own nationalities: {same_as_nationality}")


def picture_id_from_href(href: str | None) -> str | None:
    """Return the trailing picture id of an image URL, e.g. ``.../images/64071706``."""
    if not href:
        return None
    return href.rstrip("/").rsplit("/", 1)[-1]


def analyze_images(images: dict[str, dict[str, Any]], notices: list[dict[str, Any]]) -> None:
    """Report image counts per notice and whether the thumbnail is one of the images."""
    print(f"\n=== IMAGE RESPONSES ({len(images)} files) ===")
    per_notice: Counter[str] = Counter()
    image_keys: Counter[str] = Counter()
    for document in images.values():
        items = document.get("_embedded", {}).get("images", [])
        per_notice[str(len(items))] += 1
        for item in items:
            image_keys.update(item.keys())
    print_counter("images per notice:", per_notice)
    print_counter("keys of an image item:", image_keys)

    thumbnail_found = thumbnail_missing = no_thumbnail = 0
    for notice in notices:
        url_id = str(notice.get("entity_id", "")).replace("/", "-")
        href = notice.get("_links", {}).get("thumbnail", {}).get("href")
        thumbnail_id = picture_id_from_href(href)
        if thumbnail_id is None:
            no_thumbnail += 1
            continue
        items = images.get(url_id, {}).get("_embedded", {}).get("images", [])
        picture_ids = {str(item.get("picture_id")) for item in items}
        if thumbnail_id in picture_ids:
            thumbnail_found += 1
        else:
            thumbnail_missing += 1
    print(
        f"  thumbnail picture_id in /images list: yes={thumbnail_found} "
        f"no={thumbnail_missing} notice without thumbnail={no_thumbnail}"
    )


def main() -> None:
    """Load the samples and print every report."""
    lists = load_all(SAMPLES_DIR / "lists")
    notices = load_all(SAMPLES_DIR / "notices")
    images = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((SAMPLES_DIR / "images").glob("*.json"))
    }
    if not notices:
        print(f"No samples found in {SAMPLES_DIR}. Run sample_interpol_api.py first.")
        return

    analyze_lists(lists)
    analyze_notices(notices)
    analyze_images(images, notices)


if __name__ == "__main__":
    main()
