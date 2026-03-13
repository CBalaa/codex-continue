#!/usr/bin/env python3

import argparse
import os
import shutil
import stat
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "codex_npm_auto_continue"
EXECUTABLE_ASSETS = [
    ASSET_DIR / "codex-wrapper.js",
    ASSET_DIR / "codex-auto-continue-pty.py",
    ASSET_DIR / "codex-auto-continue-notify.py",
]
STATIC_ASSETS = [
    ASSET_DIR / "codex-auto-continue-web.html",
    ASSET_DIR / "codex-auto-continue-web.css",
    ASSET_DIR / "codex-auto-continue-web.js",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Which codex executable to patch (default: codex from PATH)",
    )
    parser.add_argument(
        "--install-dir",
        default=None,
        help="Package bin directory to patch directly instead of resolving from codex",
    )
    return parser.parse_args()


def resolve_launcher_path(codex_path: str) -> Path:
    resolved = Path(os.path.realpath(codex_path)).resolve()
    if resolved.name.lower() == "codex.js":
        return resolved
    raise RuntimeError(
        "Expected the resolved codex launcher to end in bin/codex.js, got "
        f"{resolved}"
    )


def resolve_install_dir(args: argparse.Namespace) -> Path:
    if args.install_dir:
        return Path(args.install_dir).resolve()

    codex_path = shutil.which(args.codex_bin)
    if not codex_path:
        raise RuntimeError(f"Could not find {args.codex_bin!r} on PATH")

    return resolve_launcher_path(codex_path).parent


def ensure_executable(path: Path) -> None:
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_executable(src: Path, dest: Path) -> None:
    shutil.copy2(src, dest)
    ensure_executable(dest)


def copy_static(src: Path, dest: Path) -> None:
    shutil.copy2(src, dest)


def install_patch(install_dir: Path) -> tuple[Path, Path]:
    launcher_path = install_dir / "codex.js"
    real_launcher_path = install_dir / "codex.real.js"

    if not launcher_path.exists():
        raise RuntimeError(f"Missing launcher at {launcher_path}")

    install_dir.mkdir(parents=True, exist_ok=True)

    if not real_launcher_path.exists():
        shutil.copy2(launcher_path, real_launcher_path)
        ensure_executable(real_launcher_path)

    copy_executable(ASSET_DIR / "codex-wrapper.js", launcher_path)
    for src in EXECUTABLE_ASSETS[1:]:
        copy_executable(src, install_dir / src.name)
    for src in STATIC_ASSETS:
        copy_static(src, install_dir / src.name)

    return launcher_path, real_launcher_path


def main() -> int:
    args = parse_args()
    install_dir = resolve_install_dir(args)
    launcher_path, real_launcher_path = install_patch(install_dir)
    print(f"Patched Codex launcher: {launcher_path}")
    print(f"Original launcher backup: {real_launcher_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
