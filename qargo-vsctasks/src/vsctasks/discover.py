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


def find_vscode_files(
    root: Path,
    extra_excludes: tuple[str, ...] = (),
) -> tuple[list[Path], list[Path]]:
    """Walk *root* once and return (tasks_files, launch_files).

    Pruning strategy (makes scanning from ~ fast):
    - Hidden directories (start with '.') are skipped entirely
    - Known noisy directories (node_modules, dist, …) are skipped
    - Any directory containing .vsctasksignore is not descended into
    - Symlinks are not followed (prevents loops)
    """
    tasks_files: list[Path] = []
    launch_files: list[Path] = []
    extra = frozenset(extra_excludes)

    for dirpath, dirs, files in os.walk(str(root), followlinks=False):
        current = Path(dirpath)

        # If this directory has a .vsctasksignore, skip it entirely
        if '.vsctasksignore' in files:
            dirs.clear()
            continue

        # When we're inside a .vscode directory, check files directly —
        # no stat() needed since os.walk already enumerated the entries.
        if current.name == '.vscode':
            if 'tasks.json' in files:
                tasks_files.append(current / 'tasks.json')
            if 'launch.json' in files:
                launch_files.append(current / 'launch.json')
            dirs.clear()  # never recurse deeper into .vscode
            continue

        # Prune directories before recursing (.vscode is allowed through)
        dirs[:] = [
            d for d in dirs
            if d not in PRUNE_DIRS
            and (d == '.vscode' or not d.startswith('.'))
            and d not in extra
        ]

    return tasks_files, launch_files


def find_tasks_files(root: Path, extra_excludes: tuple[str, ...] = ()) -> list[Path]:
    """Return all .vscode/tasks.json files under *root*."""
    tasks, _ = find_vscode_files(root, extra_excludes)
    return tasks


def find_launch_files(root: Path, extra_excludes: tuple[str, ...] = ()) -> list[Path]:
    """Return all .vscode/launch.json files under *root*."""
    _, launch = find_vscode_files(root, extra_excludes)
    return launch
