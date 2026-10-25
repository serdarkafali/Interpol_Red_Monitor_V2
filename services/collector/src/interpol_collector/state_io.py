"""Small file helpers shared by the collector's state files."""

import os
from pathlib import Path


def write_text_atomically(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` so that readers see either the old or the new file.

    The text goes to a temporary file in the same directory, is flushed to disk, and is
    then moved over the target in one step. A crash in the middle leaves the old file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
