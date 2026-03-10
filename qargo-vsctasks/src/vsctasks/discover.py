"""Filesystem traversal to find .vscode/tasks.json and .vscode/launch.json files."""

from __future__ import annotations

import os
from pathlib import Path

PRUNE_DIRS = frozenset({
    'node_modules', 'dist', 'build', 'out', 'target',
    '__pycache__', '.tox', 'venv', '.venv', 'env',
    '.next', '.nuxt', '.turbo', 'coverage',
    '.gradle', '.mvn', 'vendor', 'Pods', '.terraform',
})


def _find_vscode_file(
    root: Path,
    filename: str,
    extra_excludes: tuple[str, ...] = (),
) -> list[Path]:
    """Walk *root* and return every .vscode/<filename> that exists.

    Pruning strategy (makes scanning from ~ fast):
    - Hidden directories (start with '.') are skipped entirely
    - Known noisy directories (node_modules, dist, …) are skipped
    - Any directory containing .vsctasksignore is not descended into
    - Symlinks are not followed (prevents loops)
    """
    results: list[Path] = []
    extra = frozenset(extra_excludes)

    for dirpath, dirs, files in os.walk(str(root), followlinks=False):
        current = Path(dirpath)

        # If this directory has a .vsctasksignore, skip it entirely
        if (current / '.vsctasksignore').exists():
            dirs.clear()
            continue

        vscode_file = current / '.vscode' / filename
        if vscode_file.is_file():
            results.append(vscode_file)

        # Prune directories before recursing
        dirs[:] = [
            d for d in dirs
            if d not in PRUNE_DIRS
            and not d.startswith('.')
            and d not in extra
        ]

    return results


def find_tasks_files(root: Path, extra_excludes: tuple[str, ...] = ()) -> list[Path]:
    """Return all .vscode/tasks.json files under *root*."""
    return _find_vscode_file(root, 'tasks.json', extra_excludes)


def find_launch_files(root: Path, extra_excludes: tuple[str, ...] = ()) -> list[Path]:
    """Return all .vscode/launch.json files under *root*."""
    return _find_vscode_file(root, 'launch.json', extra_excludes)
