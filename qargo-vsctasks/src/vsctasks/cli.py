"""CLI entry point — list / run / info subcommands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .discover import find_launch_files, find_tasks_files
from .execute import execute_task
from .launch_execute import execute_compound, execute_launch
from .launch_parse import CompoundLaunch, LaunchConfig, WorkspaceLaunch, parse_launch_file
from .parse import Task, WorkspaceTasks, parse_tasks_file

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
            return list(p.parts)

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


# Entry = (id, kind, obj, workspace_obj)
#   kind: "task"     → obj: Task,           workspace_obj: WorkspaceTasks
#   kind: "launch"   → obj: LaunchConfig,   workspace_obj: WorkspaceLaunch
#   kind: "compound" → obj: CompoundLaunch, workspace_obj: WorkspaceLaunch
type _Entry = tuple[str, str, Task | LaunchConfig | CompoundLaunch, WorkspaceTasks | WorkspaceLaunch]


def _load_all(
    root: Path,
    extra_excludes: tuple[str, ...],
    quiet: bool = False,
) -> tuple[list[_Entry], dict[str, WorkspaceTasks]]:
    """Discover and parse all tasks and launch configs. Returns:
    - list of (entry_id, kind, obj, workspace_obj)
    - dict of workspace_folder_str -> WorkspaceTasks  (for preLaunchTask resolution)
    """
    tasks_files = find_tasks_files(root, extra_excludes)
    launch_files = find_launch_files(root, extra_excludes)
    if not quiet:
        print(
            f"Scanned: found {len(tasks_files)} tasks.json "
            f"and {len(launch_files)} launch.json file(s)",
            file=sys.stderr,
        )

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

    all_wl: list[WorkspaceLaunch] = []
    for lf in launch_files:
        try:
            wl = parse_launch_file(lf)
        except ValueError as e:
            if not quiet:
                print(f"Warning: {e}", file=sys.stderr)
            continue
        all_wl.append(wl)

    # Compute workspace names from all unique workspace folders
    all_folders: list[Path] = []
    seen_folders: set[Path] = set()
    for wt in all_wt:
        if wt.workspace_folder not in seen_folders:
            all_folders.append(wt.workspace_folder)
            seen_folders.add(wt.workspace_folder)
    for wl in all_wl:
        if wl.workspace_folder not in seen_folders:
            all_folders.append(wl.workspace_folder)
            seen_folders.add(wl.workspace_folder)

    name_map = _compute_workspace_names(all_folders, root)

    entries: list[_Entry] = []

    for wt in all_wt:
        ws_name = name_map.get(wt.workspace_folder, wt.workspace_folder.name)
        for task in wt.tasks:
            entry_id = _build_task_id(ws_name, task.label)
            entries.append((entry_id, 'task', task, wt))

    for wl in all_wl:
        ws_name = name_map.get(wl.workspace_folder, wl.workspace_folder.name)
        for config in wl.configs:
            entry_id = _build_launch_id(ws_name, config.name)
            entries.append((entry_id, 'launch', config, wl))
        for compound in wl.compounds:
            entry_id = _build_compound_id(ws_name, compound.name)
            entries.append((entry_id, 'compound', compound, wl))

    return entries, workspace_tasks_map


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    extra = tuple(args.exclude) if args.exclude else ()
    entries, _ = _load_all(root, extra)
    for entry_id, _, _, _ in entries:
        print(entry_id)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table

    root = Path(args.root).resolve()
    task_id_arg = args.task_id

    entries, _ = _load_all(root, (), quiet=True)

    match = None
    for eid, kind, obj, ws_obj in entries:
        if eid == task_id_arg:
            match = (kind, obj, ws_obj)
            break

    if match is None:
        print(f"Error: '{task_id_arg}' not found", file=sys.stderr)
        return 1

    kind, obj, ws_obj = match
    console = Console()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")

    if kind == 'compound':
        compound: CompoundLaunch = obj  # type: ignore[assignment]
        wl_c: WorkspaceLaunch = ws_obj  # type: ignore[assignment]
        table.add_row("Name", compound.name)
        table.add_row("Workspace", str(wl_c.workspace_folder))
        table.add_row("Configurations", ', '.join(compound.configurations))

    elif kind == 'task':
        task: Task = obj  # type: ignore[assignment]
        wt: WorkspaceTasks = ws_obj  # type: ignore[assignment]
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

    else:  # kind == 'launch'
        config: LaunchConfig = obj  # type: ignore[assignment]
        wl: WorkspaceLaunch = ws_obj  # type: ignore[assignment]
        table.add_row("Name", config.name)
        table.add_row("Workspace", str(wl.workspace_folder))
        table.add_row("Type", config.type)
        table.add_row("Request", config.request)
        if config.program:
            table.add_row("Program", config.program)
        if config.module:
            table.add_row("Module", config.module)
        if config.args:
            table.add_row("Args", ' '.join(config.args))
        table.add_row("CWD", config.cwd or "(workspace root)")
        if config.env:
            env_str = '  '.join(f"{k}={v}" for k, v in config.env.items())
            table.add_row("Env", env_str)
        if config.env_file:
            table.add_row("EnvFile", config.env_file)
        if config.pre_launch_task:
            table.add_row("PreLaunchTask", config.pre_launch_task)
        if config.request == 'attach':
            table.add_row("Note", "attach configs require a debugger — cannot run from shell")

    console.print(table)
    return 0


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
