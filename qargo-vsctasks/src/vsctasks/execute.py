"""Command construction and subprocess execution."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from .parse import Task, WorkspaceTasks
from .resolve import topological_sort
from .variables import resolve_task_variables


def _run_single_task(task: Task, inputs: list[dict]) -> int:
    """Execute a single task (no dependency handling). Returns exit code."""
    command, args, cwd = resolve_task_variables(task, inputs=inputs)

    # Compound tasks (no command, only dependsOn) are pure orchestrators
    if command is None and not args:
        return 0

    merged_env = os.environ.copy()
    merged_env.update(task.env)

    tty_fd = None
    if not sys.stdin.isatty():
        try:
            tty_fd = open('/dev/tty', 'r')
        except OSError:
            pass  # no controlling terminal (CI), inherit as-is

    try:
        if task.type == 'process':
            cmd_list = ([command] if command else []) + args
            result = subprocess.run(cmd_list, cwd=str(cwd), env=merged_env, stdin=tty_fd)
        else:
            # shell type (default)
            shell_exe = task.shell or os.environ.get('SHELL', '/bin/sh')
            full_cmd = command or ''
            if args:
                full_cmd = full_cmd + ' ' + ' '.join(shlex.quote(a) for a in args)
            result = subprocess.run(
                full_cmd,
                shell=True,
                executable=shell_exe,
                cwd=str(cwd),
                env=merged_env,
                stdin=tty_fd,
            )
    finally:
        if tty_fd:
            tty_fd.close()

    return result.returncode


def execute_task(task: Task, wt: WorkspaceTasks) -> int:
    """Resolve dependencies and execute *task*. Returns final exit code.

    Dependency resolution is scoped to the same workspace, matching VSCode behaviour
    (dependsOn labels refer to tasks within the same tasks.json).
    """
    # Build label -> Task map scoped to this workspace only
    workspace_tasks: dict[str, Task] = {t.label: t for t in wt.tasks}

    try:
        ordered = topological_sort(task, workspace_tasks)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if task.depends_order == 'parallel' and len(ordered) > 1:
        print(
            "Warning: dependsOrder=parallel is not fully supported; running sequentially",
            file=sys.stderr,
        )

    for t in ordered:
        print(f"\nRunning: {t.label}", file=sys.stderr)
        rc = _run_single_task(t, wt.inputs)
        if rc != 0:
            print(f"Task '{t.label}' exited with code {rc}", file=sys.stderr)
            return rc

    return 0
