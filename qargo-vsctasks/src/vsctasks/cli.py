"""CLI entry point — list / run / info subcommands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator

from .discover import iter_vscode_files
from .execute import execute_task
from .launch_execute import execute_compound, execute_launch
from .launch_parse import CompoundLaunch, LaunchConfig, WorkspaceLaunch, parse_launch_file
from .parse import WorkspaceTasks, parse_tasks_file

# Sigil that distinguishes launch config IDs from task IDs in the flat list.
# Mirrors VSCode's own "Run Without Debugging" prefix convention.
_LAUNCH_SIGIL = '>'
_COMPOUND_SIGIL = '>+'


# ---------------------------------------------------------------------------
# ID construction / parsing
# ---------------------------------------------------------------------------

def _build_task_id(workspace_name: str, label: str) -> str:
    return f"[{workspace_name}] {label}"


def _build_launch_id(workspace_name: str, name: str) -> str:
    return f"[{workspace_name}] {_LAUNCH_SIGIL}{name}"


def _build_compound_id(workspace_name: str, name: str) -> str:
    return f"[{workspace_name}] {_COMPOUND_SIGIL}{name}"


# Entry = (id, kind, obj, workspace_obj)
#   kind: "task"     → obj: Task,           workspace_obj: WorkspaceTasks
#   kind: "launch"   → obj: LaunchConfig,   workspace_obj: WorkspaceLaunch
#   kind: "compound" → obj: CompoundLaunch, workspace_obj: WorkspaceLaunch
type _Entry = tuple[str, str, Task | LaunchConfig | CompoundLaunch, WorkspaceTasks | WorkspaceLaunch]


def _iter_entries(
    root: Path,
    extra_excludes: tuple[str, ...],
) -> Iterator[_Entry]:
    for kind, path in iter_vscode_files(root, extra_excludes):
        ws_folder = path.parent.parent
        if kind == 'task':
            try:
                wt = parse_tasks_file(path)
            except ValueError as e:
                print(f"Warning: {e}", file=sys.stderr)
                continue
            for task in wt.tasks:
                yield _build_task_id(str(ws_folder), task.label), 'task', task, wt
        else:
            try:
                wl = parse_launch_file(path)
            except ValueError as e:
                print(f"Warning: {e}", file=sys.stderr)
                continue
            for config in wl.configs:
                yield _build_launch_id(str(ws_folder), config.name), 'launch', config, wl
            for compound in wl.compounds:
                yield _build_compound_id(str(ws_folder), compound.name), 'compound', compound, wl


def _load_all(
    root: Path,
    extra_excludes: tuple[str, ...],
) -> tuple[list[_Entry], dict[str, WorkspaceTasks]]:
    entries = list(_iter_entries(root, extra_excludes))
    workspace_tasks_map = {
        str(ws_obj.workspace_folder): ws_obj
        for _, kind, _, ws_obj in entries
        if kind == 'task'
    }
    return entries, workspace_tasks_map


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    extra = tuple(args.exclude) if args.exclude else ()
    for uid, kind, obj, ws_obj in _iter_entries(root, extra):
        ws_name = ws_obj.workspace_folder.name
        if kind == 'task':
            display = _build_task_id(ws_name, obj.label)
        elif kind == 'launch':
            display = _build_launch_id(ws_name, obj.name)
        else:
            display = _build_compound_id(ws_name, obj.name)
        print(f"{display}\t{uid}", flush=True)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    import json

    root = Path(args.root).resolve()
    task_id_arg = args.task_id

    entries, _ = _load_all(root, ())

    for eid, kind, obj, ws_obj in entries:
        if eid == task_id_arg:
            print(json.dumps(obj.raw))  # type: ignore[union-attr]
            return 0

    print(f"Error: '{task_id_arg}' not found", file=sys.stderr)
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    extra = tuple(args.exclude) if args.exclude else ()

    # Collect IDs: from positional args or stdin (one per line)
    if args.task_ids:
        task_ids = args.task_ids
    else:
        task_ids = [line.strip() for line in sys.stdin if line.strip()]

    if not task_ids:
        print("Error: no task ID provided (pass as argument or via stdin)", file=sys.stderr)
        return 1

    # Single scan for all tasks
    entries, workspace_tasks_map = _load_all(root, extra)

    # Build lookup map: id → (kind, obj, ws_obj)
    entry_map = {eid: (kind, obj, ws_obj) for eid, kind, obj, ws_obj in entries}

    exit_code = 0
    for task_id in task_ids:
        match = entry_map.get(task_id)
        if match is None:
            print(f"Error: '{task_id}' not found", file=sys.stderr)
            return 1
        kind, obj, ws_obj = match
        print(f"Running: {task_id}", file=sys.stderr)
        if kind == 'task':
            rc = execute_task(obj, ws_obj)          # type: ignore[arg-type]
        elif kind == 'compound':
            rc = execute_compound(obj, ws_obj, workspace_tasks_map)  # type: ignore[arg-type]
        else:
            rc = execute_launch(obj, ws_obj, workspace_tasks_map)  # type: ignore[arg-type]
        if rc != 0:
            return rc   # stop on first failure

    return exit_code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='vsctasks',
        description=(
            'VSCode task runner — scan, list, and execute '
            '.vscode/tasks.json tasks and .vscode/launch.json configs'
        ),
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            '--root',
            default='.',
            metavar='PATH',
            help='Root directory to scan (default: current directory)',
        )

    # list
    p_list = subparsers.add_parser('list', help='List all available tasks and launch configs')
    add_common(p_list)
    p_list.add_argument(
        '--exclude',
        action='append',
        metavar='PATTERN',
        help='Extra directory names to exclude (repeatable)',
    )

    # run
    p_run = subparsers.add_parser('run', help='Run a task or launch config')
    add_common(p_run)
    p_run.add_argument(
        '--exclude',
        action='append',
        metavar='PATTERN',
        help='Extra directory names to exclude (repeatable)',
    )
    p_run.add_argument(
        'task_ids',
        nargs='*',
        metavar='TASK_ID',
        help=(
            'One or more task / launch config IDs '
            '(e.g. "[my-repo] Build" "[my-repo] >Launch Program"). '
            'Reads one ID per line from stdin if omitted.'
        ),
    )

    # info
    p_info = subparsers.add_parser('info', help='Show task/launch config details (for fzf --preview)')
    add_common(p_info)
    p_info.add_argument('task_id', metavar='TASK_ID', help='Task or launch config ID to describe')

    return parser


def main() -> None:
    parser = _make_parser()
    args = parser.parse_args()

    dispatch = {
        'list': cmd_list,
        'run': cmd_run,
        'info': cmd_info,
    }
    handler = dispatch[args.command]
    sys.exit(handler(args))
