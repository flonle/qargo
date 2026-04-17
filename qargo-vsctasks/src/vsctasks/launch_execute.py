"""Execution of .vscode/launch.json configurations (launch request only)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
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
        # Prefer the real PTY slave path (e.g. /dev/ttys003) over the /dev/tty alias.
        # On macOS, kqueue EVFILT_READ returns EINVAL on /dev/tty but works on the
        # actual PTY slave device — which matters for prompt_toolkit / IPython.
        tty_path = None
        for check_fd in (2, 1):  # stderr first, then stdout
            if os.isatty(check_fd):
                try:
                    tty_path = os.ttyname(check_fd)
                    break
                except OSError:
                    pass
        try:
            tty_fd = os.open(tty_path or '/dev/tty', os.O_RDWR)
        except OSError:
            pass  # no controlling terminal (CI), inherit as-is

    try:
        print(f'Executing the following command: {argv}', file=sys.stderr)
        result = subprocess.run(argv, cwd=str(cwd), env=merged_env, stdin=tty_fd)
    finally:
        if tty_fd is not None:
            os.close(tty_fd)

    return result.returncode


_ANSI_COLORS = ['\033[32m', '\033[33m', '\033[34m', '\033[35m', '\033[36m']
_ANSI_RESET = '\033[0m'


def execute_compound(
    compound: CompoundLaunch,
    wl: WorkspaceLaunch,
    workspace_tasks_map: dict | None = None,
) -> int:
    """Spawn all configs in parallel, interleave prefixed output, stop on first exit."""
    config_map = {c.name: c for c in wl.configs}

    # Resolve configs and run preLaunchTasks sequentially before spawning
    resolved: list[tuple[str, list[str], Path, dict]] = []
    for config_name in compound.configurations:
        config = config_map.get(config_name)
        if config is None:
            print(f"Error: compound references unknown config '{config_name}'", file=sys.stderr)
            return 1
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
        resolved.append((config.name, argv, cwd, merged_env))

    use_color = sys.stdout.isatty()

    # Spawn all processes
    procs: list[tuple[str, subprocess.Popen]] = []
    for name, argv, cwd, env in resolved:
        print(f"Spawning: {argv}", file=sys.stderr)
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        procs.append((name, proc))

    exit_queue: queue.Queue[tuple[str, int]] = queue.Queue()

    def _reader(name: str, proc: subprocess.Popen, color: str) -> None:
        prefix = f"{color}[{name}]{_ANSI_RESET} " if use_color else f"[{name}] "
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            sys.stdout.write(prefix + raw_line.decode('utf-8', errors='replace').rstrip('\r\n') + '\n')
            sys.stdout.flush()
        exit_queue.put((name, proc.wait()))

    threads = []
    for i, (name, proc) in enumerate(procs):
        color = _ANSI_COLORS[i % len(_ANSI_COLORS)]
        t = threading.Thread(target=_reader, args=(name, proc, color), daemon=True)
        t.start()
        threads.append(t)

    # Block until first process exits
    first_name, rc = exit_queue.get()
    print(f"\n[{first_name}] exited with code {rc}", file=sys.stderr)

    # Terminate remaining processes
    for _, proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    for t in threads:
        t.join(timeout=2)

    return rc


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
