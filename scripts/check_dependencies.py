"""Run deptry in every workspace package: each must declare what its code imports.

The uv workspace installs every package into one shared .venv, so an import that a
package does not declare in its own pyproject.toml still works locally and only fails
inside that service's Docker image. deptry reads one pyproject.toml at a time, so it
runs once per package, in the package's folder (its [tool.deptry] settings live there).

Run from the repository root:
    uv run python scripts/check_dependencies.py
"""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def workspace_packages() -> list[Path]:
    """The package folders matched by [tool.uv.workspace] members, in a stable order."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns: list[str] = config["tool"]["uv"]["workspace"]["members"]
    folders = {path for pattern in patterns for path in ROOT.glob(pattern)}
    return sorted(path for path in folders if (path / "pyproject.toml").is_file())


def main() -> int:
    failed: list[str] = []
    for package in workspace_packages():
        name = package.relative_to(ROOT).as_posix()
        print(f"deptry {name}", flush=True)
        result = subprocess.run([sys.executable, "-m", "deptry", "."], cwd=package, check=False)
        if result.returncode != 0:
            failed.append(name)
    if failed:
        print(f"Dependency issues in: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
