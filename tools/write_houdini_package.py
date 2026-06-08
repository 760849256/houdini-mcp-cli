"""Write a Houdini package file for this bridge checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def package_payload(release_root: Path | str = ROOT) -> dict:
    root = Path(release_root).resolve()
    return {
        "env": [
            {
                "BLIB_HOUDINI_BRIDGE": root.as_posix(),
            }
        ],
        "path": "$BLIB_HOUDINI_BRIDGE",
    }


def validate_release_root(release_root: Path | str) -> None:
    root = Path(release_root)
    required = [
        "toolbar/Blib_Houdini_Bridge.shelf",
        "scripts/python/blib_hou_bridge/__init__.py",
        "scripts/python/blib_hou_mcp/__init__.py",
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        raise ValueError("Bridge release root is missing required paths: %s" % ", ".join(missing))


def write_package(output: Path | str, release_root: Path | str = ROOT) -> Path:
    validate_release_root(release_root)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package_payload(release_root), indent=4) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="write_houdini_package",
        description="Create a Blib_Houdini_Bridge.json package for this machine.",
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Bridge release root. Defaults to the directory containing this tools folder.",
    )
    parser.add_argument(
        "--output",
        default="Blib_Houdini_Bridge.local.json",
        help="Package JSON path to write. Use a Houdini packages directory for install.",
    )
    parser.add_argument("--print", action="store_true", help="Print the package JSON after writing it.")
    args = parser.parse_args(argv)

    output = write_package(args.output, args.root)
    payload = package_payload(args.root)
    if args.print:
        print(json.dumps(payload, indent=4))
    else:
        print("Wrote %s for bridge root %s" % (output, Path(args.root).resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
