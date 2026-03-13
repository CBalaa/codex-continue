#!/usr/bin/env python3

import argparse
import os
import shutil
import stat
from pathlib import Path

PATCHED_FILES = [
    "codex-auto-continue-pty.py",
    "codex-auto-continue-notify.py",
    "codex-auto-continue-web.html",
    "codex-auto-continue-web.css",
    "codex-auto-continue-web.js",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Which codex executable to restore (default: codex from PATH)",
    )
    parser.add_argument(
        "--install-dir",
        default=None,
        help="Package bin directory to restore directly instead of resolving from codex",
    )
    parser.add_argument(
        "--keep-backup",
        action="store_true",
        help="Keep bin/codex.real.js after restoring bin/codex.js",
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


def remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def uninstall_patch(install_dir: Path, keep_backup: bool) -> tuple[Path, Path]:
    launcher_path = install_dir / "codex.js"
    real_launcher_path = install_dir / "codex.real.js"

    if not launcher_path.exists():
        raise RuntimeError(f"Missing launcher at {launcher_path}")

    if not real_launcher_path.exists():
        raise RuntimeError(
            f"Missing backup launcher at {real_launcher_path}; nothing to restore"
        )

    shutil.copy2(real_launcher_path, launcher_path)
    ensure_executable(launcher_path)

    for filename in PATCHED_FILES:
        remove_if_exists(install_dir / filename)

    if not keep_backup:
        real_launcher_path.unlink()

    return launcher_path, real_launcher_path


def main() -> int:
    args = parse_args()
    install_dir = resolve_install_dir(args)
    launcher_path, real_launcher_path = uninstall_patch(
        install_dir, keep_backup=args.keep_backup
    )
    print(f"Restored Codex launcher: {launcher_path}")
    if args.keep_backup:
        print(f"Kept launcher backup: {real_launcher_path}")
    else:
        print(f"Removed launcher backup: {real_launcher_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
