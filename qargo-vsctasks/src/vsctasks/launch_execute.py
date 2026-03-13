"""Execution of .vscode/launch.json configurations (launch request only)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .launch_parse import CompoundLaunch, LaunchConfig, WorkspaceLaunch
from .variables import resolve_variables


def _load_env_file(env_file_path: str, workspace_folder: Path) -> dict[str, str]:
    """Parse a simple KEY=value .env file. Ignores blank lines and # comments."""
    env: dict[str, str] = {}
    path = Path(env_file_path)
    if not path.is_absolute():
        path = workspace_folder / path
    if not path.is_file():
        print(f"Warning: envFile '{path}' not found", file=sys.stderr)
        return env
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, _, value = line.partition('=')
            value = value.strip()
            # Strip surrounding single or double quotes from the value.
            # VSCode does this too when loading envFile, so we mirror that behaviour
            # to ensure env vars reach the process in the same form they would under VSCode.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            env[key.strip()] = value
    return env


def _resolve_config_variables(
    config: LaunchConfig,
    inputs: list[dict],
) -> tuple[str | None, str | None, list[str], Path, dict[str, str]]:
    """Resolve VSCode variables in a LaunchConfig.

    Returns (program, module, args, cwd, env).
    """
    input_cache: dict[str, str] = {}
    workspace = config.workspace_folder

    raw_cwd = config.cwd or ''
    if raw_cwd:
        resolved_cwd = Path(
            resolve_variables(raw_cwd, workspace, inputs=inputs, _input_cache=input_cache)
        )
    else:
        resolved_cwd = workspace

    def res(text: str) -> str:
        return resolve_variables(
            text, workspace, cwd=resolved_cwd, inputs=inputs, _input_cache=input_cache
        )

    resolved_program = res(config.program) if config.program else None
    resolved_module = res(config.module) if config.module else None
    resolved_args = [res(a) for a in config.args]
    resolved_env = {k: res(v) for k, v in config.env.items()}

    if config.env_file:
        resolved_env_file = res(config.env_file)
        file_env = _load_env_file(resolved_env_file, workspace)
        # config.env takes precedence over envFile
        resolved_env = {**file_env, **resolved_env}

    return resolved_program, resolved_module, resolved_args, resolved_cwd, resolved_env


_SUPPORTED_TYPES = (
    'node', 'pwa-node', 'node2',
    'python', 'debugpy', 'ms-python.debugpy', 'ms-python.python',
    'go',
    'shell',
    'coreclr', 'clr',
    'cppdbg', 'cppvsdbg', 'lldb', 'gdb',
)


def _translate_to_argv(
    config: LaunchConfig,
    program: str | None,
    module: str | None,
    args: list[str],
) -> list[str] | None:
    """Convert a launch config to an argv list. Returns None if not runnable."""
    t = config.type.lower()

    if t in ('node', 'pwa-node', 'node2'):
        if not program:
            print(f"Warning: '{config.name}': no program specified; skipping", file=sys.stderr)
            return None
        return ['node', program] + args

    if t in ('python', 'debugpy', 'ms-python.debugpy', 'ms-python.python'):
        if module:
            return ['python', '-m', module] + args
        if program:
            return ['python', program] + args
        print(f"Warning: '{config.name}': no program or module specified; skipping", file=sys.stderr)
        return None

    if t == 'go':
        mode = config.raw.get('mode', 'debug')
        if mode == 'test':
            return ['go', 'test'] + args
        if program:
            return ['go', 'run', program] + args
        return ['go', 'run', '.'] + args

    if t == 'shell':
        command = config.raw.get('command', '')
        if not command:
            print(f"Warning: '{config.name}': no command specified; skipping", file=sys.stderr)
            return None
        return [command] + args

    if t in ('coreclr', 'clr'):
        if program:
            return [program] + args
        return ['dotnet', 'run'] + args

    if t in ('cppdbg', 'cppvsdbg', 'lldb', 'gdb'):
        if not program:
            print(f"Warning: '{config.name}': no program specified; skipping", file=sys.stderr)
            return None
        return [program] + args

    print(
        f"Warning: '{config.name}' has unsupported type '{config.type}'; skipping.\n"
        f"  Supported types: {', '.join(_SUPPORTED_TYPES)}",
        file=sys.stderr,
    )
    return None


def execute_launch(
    config: LaunchConfig,
    wl: WorkspaceLaunch,
    workspace_tasks_map: dict | None = None,
) -> int:
    """Execute a launch configuration. Returns exit code.

    *workspace_tasks_map* is a dict[str, WorkspaceTasks] keyed by workspace folder path string,
    used to resolve preLaunchTask references.
    """
    if config.request != 'launch':
        print(
            f"Error: '{config.name}' is an attach configuration.\n"
            f"  Attaching to a running process requires a debugger and cannot be run from the shell.",
            file=sys.stderr,
        )
        return 1

    # Run preLaunchTask if set
    if config.pre_launch_task:
        rc = _run_pre_launch_task(config, workspace_tasks_map)
        if rc != 0:
            return rc

    program, module, args, cwd, extra_env = _resolve_config_variables(config, wl.inputs)
    argv = _translate_to_argv(config, program, module, args)
    if argv is None:
        return 1

    merged_env = os.environ.copy()
    merged_env.update(extra_env)

    tty_fd = None
    if not sys.stdin.isatty():
        try:
            tty_fd = open('/dev/tty', 'r')
        except OSError:
            pass  # no controlling terminal (CI), inherit as-is

    try:
        result = subprocess.run(argv, cwd=str(cwd), env=merged_env, stdin=tty_fd)
    finally:
        if tty_fd:
            tty_fd.close()

    return result.returncode


def execute_compound(
    compound: CompoundLaunch,
    wl: WorkspaceLaunch,
    workspace_tasks_map: dict | None = None,
) -> int:
    """Execute a compound launch config by running each referenced config in sequence."""
    config_map = {c.name: c for c in wl.configs}
    for config_name in compound.configurations:
        config = config_map.get(config_name)
        if config is None:
            print(f"Error: compound references unknown config '{config_name}'", file=sys.stderr)
            return 1
        rc = execute_launch(config, wl, workspace_tasks_map)
        if rc != 0:
            return rc
    return 0


def _run_pre_launch_task(config: LaunchConfig, workspace_tasks_map: dict | None) -> int:
    """Run the preLaunchTask referenced by *config*. Returns exit code."""
    from .execute import execute_task

    task_name = config.pre_launch_task
    if workspace_tasks_map is None:
        print(
            f"Warning: preLaunchTask '{task_name}' found but no tasks.json available; skipping",
            file=sys.stderr,
        )
        return 0

    wt = workspace_tasks_map.get(str(config.workspace_folder))
    if wt is None:
        print(
            f"Warning: preLaunchTask '{task_name}' found but no tasks.json in "
            f"workspace '{config.workspace_folder}'; skipping",
            file=sys.stderr,
        )
        return 0

    task_map = {t.label: t for t in wt.tasks}
    pre_task = task_map.get(task_name)
    if pre_task is None:
        print(
            f"Warning: preLaunchTask '{task_name}' not found in tasks.json; skipping",
            file=sys.stderr,
        )
        return 0

    print(f"Running preLaunchTask: {task_name}", file=sys.stderr)
    rc = execute_task(pre_task, wt)
    if rc != 0:
        print(f"preLaunchTask '{task_name}' failed with exit code {rc}", file=sys.stderr)
    return rc
