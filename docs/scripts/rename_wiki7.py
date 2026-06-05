#!/usr/bin/env python3
"""
Rename Citizen → Wiki7 inside a target skin directory.
Case-aware: citizen→wiki7, Citizen→Wiki7, CITIZEN→WIKI7.
Skips binaries and dotfile/.git dirs.
Renames file/dir names AFTER content edits.

Usage:
    python3 rename_wiki7.py [target-skin-dir]

If target-skin-dir is omitted, defaults to
docker/skins/Wiki7 relative to the repository root (this script's
two parent directories up).

Used during the Wiki7 ← Citizen re-fork — see
docs/wiki7-skin-customization.md for the full recipe.
"""
import os
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "docker" / "skins" / "Wiki7"
ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_ROOT

if not ROOT.is_dir():
    sys.exit(f"target skin dir does not exist: {ROOT}")

BINARY_SUFFIXES = {
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico',
    '.pdf', '.zip', '.tar', '.gz',
    '.mp3', '.mp4', '.webm',
}

SKIP_DIRS = {'.git', '.github', 'node_modules', 'vendor', 'tests'}

# Order matters: do uppercase first, then Title, then lower (so partial matches don't collide).
REPLACEMENTS = [
    ('CITIZEN', 'WIKI7'),
    ('Citizen', 'Wiki7'),
    ('citizen', 'wiki7'),
]


def replace_all(text: str) -> str:
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    # Heuristic: read first 4KB; if null bytes, treat as binary.
    try:
        with path.open('rb') as f:
            chunk = f.read(4096)
        return b'\x00' in chunk
    except OSError:
        return True


changed_files = 0
edited_files = 0

# 1) Edit contents of text files.
for dirpath, dirnames, filenames in os.walk(ROOT):
    # Prune unwanted dirs in-place.
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fname in filenames:
        p = Path(dirpath) / fname
        if is_binary(p):
            continue
        try:
            original = p.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        new = replace_all(original)
        if new != original:
            p.write_text(new, encoding='utf-8')
            edited_files += 1

# 2) Rename files and dirs (bottom-up so paths stay valid).
for dirpath, dirnames, filenames in os.walk(ROOT, topdown=False):
    for name in filenames + dirnames:
        old_path = Path(dirpath) / name
        new_name = replace_all(name)
        if new_name != name:
            new_path = Path(dirpath) / new_name
            if new_path.exists():
                print(f"WARN: target exists, skipping rename: {new_path}", file=sys.stderr)
                continue
            old_path.rename(new_path)
            changed_files += 1

print(f"edited contents: {edited_files} files")
print(f"renamed paths:   {changed_files} entries")
