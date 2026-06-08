"""Install the Blib Houdini Bridge package into a Houdini user packages folder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import write_houdini_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "Blib_Houdini_Bridge.json"


def discover_package_dirs(home: Path | str | None = None) -> list[Path]:
    user_home = Path(home).expanduser() if home is not None else Path.home()
    documents = user_home / "Documents"
    if not documents.exists():
        return []
    candidates = []
    for path in documents.iterdir():
        if path.is_dir() and path.name.lower().startswith("houdini"):
            candidates.append(path / "packages")
    return sorted(candidates, key=lambda item: item.as_posix().lower(), reverse=True)


def install_package(
    release_root: Path | str = ROOT,
    packages_dir: Path | str | None = None,
    home: Path | str | None = None,
    dry_run: bool = False,
) -> dict:
    root = Path(release_root).resolve()
    write_houdini_package.validate_release_root(root)

    discovered = discover_package_dirs(home)
    target_dir = Path(packages_dir).expanduser() if packages_dir is not None else (discovered[0] if discovered else None)
    if target_dir is None:
        local_output = root / "Blib_Houdini_Bridge.local.json"
        if not dry_run:
            write_houdini_package.write_package(local_output, root)
        return {
            "installed": False,
            "target": None,
            "local_package": str(local_output),
            "discovered_package_dirs": [str(path) for path in discovered],
            "next_action": "No Houdini user package folder was found. Copy the local package file into Documents\\houdini<version>\\packages\\Blib_Houdini_Bridge.json.",
        }

    target = target_dir / PACKAGE_NAME
    if not dry_run:
        write_houdini_package.write_package(target, root)
    return {
        "installed": not dry_run,
        "target": str(target),
        "local_package": None,
        "discovered_package_dirs": [str(path) for path in discovered],
        "next_action": "Start Houdini, open the Blib Bridge shelf, and click Bridge.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_houdini_package",
        description="Create and install Blib_Houdini_Bridge.json into a Houdini user packages folder.",
    )
    parser.add_argument("--root", default=str(ROOT), help="Bridge release root. Defaults to this checkout.")
    parser.add_argument("--packages-dir", help="Houdini packages directory to write into.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without creating files.")
    args = parser.parse_args(argv)

    result = install_package(args.root, args.packages_dir, dry_run=args.dry_run)
    if result["target"]:
        verb = "Would write" if args.dry_run else "Installed"
        print("%s %s" % (verb, result["target"]))
    else:
        verb = "Would write" if args.dry_run else "Wrote"
        print("%s %s" % (verb, result["local_package"]))
    print(result["next_action"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
