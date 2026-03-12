#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import stat
from pathlib import Path

WINDOWS_SHIM_SUFFIXES = {".cmd", ".ps1"}
WINDOWS_SHIM_TOKENS = {
    "%~dp0": None,
    "%dp0%": None,
    "$basedir": None,
    "${basedir}": None,
}


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


def resolve_windows_shim_target(shim_path: Path) -> Path:
    text = shim_path.read_text(encoding="utf-8", errors="ignore")
    patterns = [
        r'["\']([^"\'\r\n]*node_modules[^"\'\r\n]*@openai[^"\'\r\n]*codex[^"\'\r\n]*bin[^"\'\r\n]*codex\.js)["\']',
        r'["\']([^"\'\r\n]*codex\.js)["\']',
    ]
    shim_dir = str(shim_path.parent)

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        raw_target = match.group(1)
        for token in WINDOWS_SHIM_TOKENS:
            raw_target = raw_target.replace(token, shim_dir)

        candidate = Path(raw_target)
        if not candidate.is_absolute():
            candidate = shim_path.parent / candidate

        return candidate.resolve()

    raise RuntimeError(
        "Could not resolve the npm package launcher from Windows shim "
        f"{shim_path}"
    )


def resolve_launcher_path(codex_path: str) -> Path:
    resolved = Path(os.path.realpath(codex_path)).resolve()
    if resolved.name.lower() == "codex.js":
        return resolved

    if os.name == "nt" and resolved.suffix.lower() in WINDOWS_SHIM_SUFFIXES:
        launcher_path = resolve_windows_shim_target(resolved)
        if launcher_path.name.lower() == "codex.js":
            return launcher_path
        raise RuntimeError(
            "Expected the Windows shim to resolve to bin/codex.js, got "
            f"{launcher_path}"
        )

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

    remove_if_exists(install_dir / "codex-auto-continue-pty.py")
    remove_if_exists(install_dir / "codex-auto-continue-notify.py")

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
