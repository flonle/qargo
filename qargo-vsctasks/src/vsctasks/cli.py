"""CLI entry point — list / run / info subcommands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .discover import find_tasks_files
from .execute import execute_task
from .parse import Task, WorkspaceTasks, parse_tasks_file


# ---------------------------------------------------------------------------
# Workspace disambiguation
# ---------------------------------------------------------------------------

def _build_task_id(workspace_name: str, label: str) -> str:
    return f"[{workspace_name}] {label}"


def _parse_task_id(task_id: str) -> tuple[str, str]:
    """Split '[workspace] Label' into (workspace, label)."""
    if task_id.startswith('['):
        end = task_id.find('] ')
        if end != -1:
            return task_id[1:end], task_id[end + 2:]
    return '', task_id


def _compute_workspace_names(
    workspace_folders: list[Path],
    root: Path,
) -> dict[Path, str]:
    """Compute the shortest unambiguous workspace name for each folder.

    Starts with the basename; if collisions exist, adds more path components
    relative to *root* (e.g. 'company/backend' vs 'personal/backend').
    """
    names: dict[Path, str] = {}

    # Compute relative paths from root
    def rel_parts(p: Path) -> list[str]:
        try:
            return list(p.relative_to(root).parts)
        except ValueError:
            return p.parts

    # Try increasing suffix length until all names are unique
    max_depth = max((len(rel_parts(p)) for p in workspace_folders), default=1)
    for depth in range(1, max_depth + 1):
        names = {}
        for p in workspace_folders:
            parts = rel_parts(p)
            suffix_parts = parts[-depth:] if len(parts) >= depth else parts
            names[p] = '/'.join(suffix_parts)

        # Check for collisions
        seen: dict[str, int] = {}
        for name in names.values():
            seen[name] = seen.get(name, 0) + 1
        if all(v == 1 for v in seen.values()):
            break

    return names


def _load_all(root: Path, extra_excludes: tuple[str, ...], quiet: bool = False) -> tuple[
    list[tuple[str, Task, WorkspaceTasks]],
    dict[str, WorkspaceTasks],
]:
    """Discover and parse all tasks. Returns:
    - list of (task_id, task, workspace_tasks)
    - dict of workspace_folder_str -> WorkspaceTasks
    """
    tasks_files = find_tasks_files(root, extra_excludes)
    if not quiet:
        print(f"Scanned: found {len(tasks_files)} tasks.json file(s)", file=sys.stderr)

    workspace_tasks_map: dict[str, WorkspaceTasks] = {}
    all_wt: list[WorkspaceTasks] = []

    for tf in tasks_files:
        try:
            wt = parse_tasks_file(tf)
        except ValueError as e:
            if not quiet:
                print(f"Warning: {e}", file=sys.stderr)
            continue
        key = str(wt.workspace_folder)
        workspace_tasks_map[key] = wt
        all_wt.append(wt)

    folders = [wt.workspace_folder for wt in all_wt]
    name_map = _compute_workspace_names(folders, root)

    entries: list[tuple[str, Task, WorkspaceTasks]] = []
    for wt in all_wt:
        ws_name = name_map.get(wt.workspace_folder, wt.workspace_folder.name)
        for task in wt.tasks:
            task_id = _build_task_id(ws_name, task.label)
            entries.append((task_id, task, wt))

    return entries, workspace_tasks_map


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    extra = tuple(args.exclude) if args.exclude else ()
    entries, _ = _load_all(root, extra)
    for task_id, _, _ in entries:
        print(task_id)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table

    root = Path(args.root).resolve()
    task_id_arg = args.task_id

    entries, _ = _load_all(root, (), quiet=True)

    match = None
    for tid, task, wt in entries:
        if tid == task_id_arg:
            match = (task, wt)
            break

    if match is None:
        print(f"Error: task '{task_id_arg}' not found", file=sys.stderr)
        return 1

    task, wt = match
    console = Console()

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")

    table.add_row("Label", task.label)
    table.add_row("Workspace", str(wt.workspace_folder))
    table.add_row("Type", task.type)
    table.add_row("Command", task.command or "(none)")
    if task.args:
        table.add_row("Args", ' '.join(task.args))
    table.add_row("CWD", task.cwd or "(workspace root)")
    if task.shell:
        table.add_row("Shell", task.shell)
    if task.env:
        env_str = '  '.join(f"{k}={v}" for k, v in task.env.items())
        table.add_row("Env", env_str)
    if task.depends_on:
        table.add_row("DependsOn", ', '.join(task.depends_on))
        table.add_row("DependsOrder", task.depends_order)
    if task.group:
        table.add_row("Group", task.group)

    console.print(table)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    extra = tuple(args.exclude) if args.exclude else ()

    # Get task ID from positional arg or stdin
    if args.task_id:
        task_id_arg = args.task_id
    else:
        task_id_arg = sys.stdin.readline().strip()

    if not task_id_arg:
        print("Error: no task ID provided (pass as argument or via stdin)", file=sys.stderr)
        return 1

    entries, _ = _load_all(root, extra)

    # Find the matching task
    match_task = None
    match_wt = None
    for tid, task, wt in entries:
        if tid == task_id_arg:
            match_task = task
            match_wt = wt
            break

    if match_task is None:
        print(f"Error: task '{task_id_arg}' not found", file=sys.stderr)
        return 1

    print(f"Running: {task_id_arg}", file=sys.stderr)
    return execute_task(match_task, match_wt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='vsctasks',
        description='VSCode task runner — scan, list, and execute .vscode/tasks.json tasks',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Shared --root and --exclude options
    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            '--root',
            default='.',
            metavar='PATH',
            help='Root directory to scan (default: current directory)',
        )

    # list
    p_list = subparsers.add_parser('list', help='List all available tasks')
    add_common(p_list)
    p_list.add_argument(
        '--exclude',
        action='append',
        metavar='PATTERN',
        help='Extra directory names to exclude (repeatable)',
    )

    # run
    p_run = subparsers.add_parser('run', help='Run a task')
    add_common(p_run)
    p_run.add_argument(
        '--exclude',
        action='append',
        metavar='PATTERN',
        help='Extra directory names to exclude (repeatable)',
    )
    p_run.add_argument(
        'task_id',
        nargs='?',
        default=None,
        metavar='TASK_ID',
        help='Task ID (e.g. "[my-repo] Build"). Reads from stdin if omitted.',
    )

    # info
    p_info = subparsers.add_parser('info', help='Show task details (for fzf --preview)')
    add_common(p_info)
    p_info.add_argument('task_id', metavar='TASK_ID', help='Task ID to describe')

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
