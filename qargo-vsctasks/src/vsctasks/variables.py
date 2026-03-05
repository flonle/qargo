"""VSCode variable resolution for task commands, args, and cwd."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _prompt_input(input_def: dict) -> str:
    """Interactively prompt the user for an ${input:name} value."""
    input_type = input_def.get('type', 'promptString')
    description = input_def.get('description', input_def.get('id', ''))

    if input_type == 'promptString':
        default = input_def.get('default', '')
        prompt = f"Input '{input_def['id']}' — {description}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        print(prompt, end='', flush=True, file=sys.stderr)
        value = sys.stdin.readline().rstrip('\n')
        return value if value else default

    if input_type == 'pickString':
        options = input_def.get('options', [])
        default = input_def.get('default', '')
        print(f"\nInput '{input_def['id']}' — {description}", file=sys.stderr)
        for i, opt in enumerate(options):
            label = opt if isinstance(opt, str) else opt.get('label', str(opt))
            marker = ' (default)' if label == default else ''
            print(f"  {i + 1}. {label}{marker}", file=sys.stderr)
        print("Choice [enter for default]: ", end='', flush=True, file=sys.stderr)
        raw = sys.stdin.readline().rstrip('\n')
        if not raw:
            return default
        try:
            idx = int(raw) - 1
            opt = options[idx]
            return opt if isinstance(opt, str) else opt.get('value', opt.get('label', ''))
        except (ValueError, IndexError):
            return raw

    # Unknown input type — prompt as string
    print(f"Input '{input_def['id']}': ", end='', flush=True, file=sys.stderr)
    return sys.stdin.readline().rstrip('\n')


def resolve_variables(
    text: str,
    workspace_folder: Path,
    cwd: Path | None = None,
    inputs: list[dict] | None = None,
    _input_cache: dict[str, str] | None = None,
) -> str:
    """Replace VSCode variables in *text* with their resolved values.

    *_input_cache* is shared across multiple calls within the same task execution
    so that the user is only prompted once per input variable.
    """
    if _input_cache is None:
        _input_cache = {}

    effective_cwd = cwd or workspace_folder

    def replace(match: re.Match) -> str:
        var = match.group(0)

        # Simple variables
        if var == '${workspaceFolder}':
            return str(workspace_folder)
        if var == '${workspaceFolderBasename}':
            return workspace_folder.name
        if var == '${cwd}':
            return str(effective_cwd)
        if var == '${pathSeparator}':
            return os.sep

        # ${env:VAR}
        env_match = re.fullmatch(r'\$\{env:([^}]+)\}', var)
        if env_match:
            return os.environ.get(env_match.group(1), '')

        # ${input:name}
        input_match = re.fullmatch(r'\$\{input:([^}]+)\}', var)
        if input_match:
            name = input_match.group(1)
            if name in _input_cache:
                return _input_cache[name]
            # Look up in inputs array
            input_def = None
            if inputs:
                for inp in inputs:
                    if inp.get('id') == name:
                        input_def = inp
                        break
            if input_def is None:
                input_def = {'id': name, 'type': 'promptString', 'description': name}
            value = _prompt_input(input_def)
            _input_cache[name] = value
            return value

        # ${config:...} — warn and leave blank
        config_match = re.fullmatch(r'\$\{config:[^}]+\}', var)
        if config_match:
            import sys as _sys
            print(f"Warning: {var} is VSCode-specific and cannot be resolved; using empty string", file=_sys.stderr)
            return ''

        # Unknown variable — leave as-is
        return var

    return re.sub(r'\$\{[^}]+\}', replace, text)


def resolve_task_variables(
    task,
    inputs: list[dict] | None = None,
) -> tuple[str | None, list[str], Path]:
    """Resolve all variables in *task* command, args, and cwd.

    Returns (resolved_command, resolved_args, resolved_cwd).
    """
    input_cache: dict[str, str] = {}
    workspace = task.workspace_folder

    # Resolve cwd first (needed for ${cwd} in other fields)
    raw_cwd = task.cwd or ''
    if raw_cwd:
        resolved_cwd = Path(resolve_variables(raw_cwd, workspace, inputs=inputs, _input_cache=input_cache))
    else:
        resolved_cwd = workspace

    def res(text: str) -> str:
        return resolve_variables(text, workspace, cwd=resolved_cwd, inputs=inputs, _input_cache=input_cache)

    resolved_command = res(task.command) if task.command else None
    resolved_args = [res(a) for a in task.args]

    return resolved_command, resolved_args, resolved_cwd
