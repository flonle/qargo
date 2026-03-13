"""JSONC parsing and Task dataclass."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    label: str
    type: str                       # "shell" | "process"
    command: str | None
    args: list[str]
    cwd: str | None                 # from options.cwd
    env: dict[str, str]             # from options.env
    shell: str | None               # from options.shell.executable
    depends_on: list[str]
    depends_order: str              # "sequence" | "parallel"
    group: str | None
    workspace_folder: Path
    raw: dict


@dataclass
class WorkspaceTasks:
    tasks: list[Task]
    inputs: list[dict]              # raw inputs array for ${input:name} resolution
    workspace_folder: Path


def _strip_jsonc_comments(text: str) -> str:
    """Remove // line comments and /* */ block comments from JSON-like text."""
    # Remove block comments first (non-greedy)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove line comments (but not inside strings)
    # Simple approach: remove // ... to end of line when not in a string
    result = []
    i = 0
    in_string = False
    while i < len(text):
        c = text[i]
        if in_string:
            if c == '\\':
                result.append(c)
                i += 1
                if i < len(text):
                    result.append(text[i])
                    i += 1
                continue
            if c == '"':
                in_string = False
            result.append(c)
            i += 1
        else:
            if c == '"':
                in_string = True
                result.append(c)
                i += 1
            elif text[i:i+2] == '//':
                # Skip to end of line
                while i < len(text) and text[i] != '\n':
                    i += 1
            else:
                result.append(c)
                i += 1
    return ''.join(result)


def _parse_group(group_raw) -> str | None:
    if group_raw is None:
        return None
    if isinstance(group_raw, str):
        return group_raw
    if isinstance(group_raw, dict):
        return group_raw.get('kind')
    return None


def parse_tasks_file(path: Path) -> WorkspaceTasks:
    """Parse a tasks.json file and return WorkspaceTasks."""
    workspace_folder = path.parent.parent  # .vscode/../ == workspace root
    text = path.read_text(encoding='utf-8')
    text = _strip_jsonc_comments(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse {path}: {e}") from e

    raw_tasks = data.get('tasks', [])
    inputs = data.get('inputs', [])

    tasks = []
    for raw in raw_tasks:
        label = raw.get('label', '')
        task_type = raw.get('type', 'shell')
        command = raw.get('command')
        args = raw.get('args', [])
        # Ensure args are strings
        args = [str(a) if not isinstance(a, str) else a for a in args]

        options = raw.get('options', {})
        cwd = options.get('cwd') if options else None
        env = options.get('env', {}) if options else {}
        if env:
            env = {k: str(v) for k, v in env.items()}

        shell_opts = options.get('shell', {}) if options else {}
        shell_exe = shell_opts.get('executable') if shell_opts else None

        depends_raw = raw.get('dependsOn', [])
        if isinstance(depends_raw, str):
            depends_on = [depends_raw]
        else:
            depends_on = list(depends_raw)

        depends_order = raw.get('dependsOrder', 'sequence')
        group = _parse_group(raw.get('group'))

        tasks.append(Task(
            label=label,
            type=task_type,
            command=command,
            args=args,
            cwd=cwd,
            env=env,
            shell=shell_exe,
            depends_on=depends_on,
            depends_order=depends_order,
            group=group,
            workspace_folder=workspace_folder,
            raw=raw,
        ))

    return WorkspaceTasks(
        tasks=tasks,
        inputs=inputs,
        workspace_folder=workspace_folder,
    )
