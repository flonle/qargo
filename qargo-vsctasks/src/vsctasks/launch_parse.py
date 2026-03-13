"""JSONC parsing for .vscode/launch.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .parse import _strip_jsonc_comments


@dataclass
class LaunchConfig:
    name: str
    type: str           # "node", "python", "go", "shell", "cppdbg", etc.
    request: str        # "launch" | "attach"
    program: str | None
    module: str | None  # for Python -m <module>
    args: list[str]
    cwd: str | None
    env: dict[str, str]
    env_file: str | None    # path to a .env file
    pre_launch_task: str | None
    workspace_folder: Path
    raw: dict           # full raw config dict for type-specific fallbacks


@dataclass
class CompoundLaunch:
    name: str
    configurations: list[str]   # config names (not full IDs) within same workspace
    workspace_folder: Path
    raw: dict


@dataclass
class WorkspaceLaunch:
    configs: list[LaunchConfig]
    compounds: list[CompoundLaunch]
    inputs: list[dict]          # raw inputs array for ${input:name} resolution
    workspace_folder: Path


def parse_launch_file(path: Path) -> WorkspaceLaunch:
    """Parse a launch.json file and return WorkspaceLaunch."""
    workspace_folder = path.parent.parent  # .vscode/../ == workspace root
    text = path.read_text(encoding='utf-8')
    text = _strip_jsonc_comments(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse {path}: {e}") from e

    raw_configs = data.get('configurations', [])
    inputs = data.get('inputs', [])

    configs = []
    for raw in raw_configs:
        name = raw.get('name', '')
        config_type = raw.get('type', '')
        request = raw.get('request', 'launch')

        program = raw.get('program')
        module = raw.get('module')

        args_raw = raw.get('args', [])
        args = [str(a) if not isinstance(a, str) else a for a in args_raw]

        cwd = raw.get('cwd')

        env_raw = raw.get('env', {})
        env = {k: str(v) for k, v in env_raw.items()} if env_raw else {}

        env_file = raw.get('envFile')
        pre_launch_task = raw.get('preLaunchTask')

        configs.append(LaunchConfig(
            name=name,
            type=config_type,
            request=request,
            program=program,
            module=module,
            args=args,
            cwd=cwd,
            env=env,
            env_file=env_file,
            pre_launch_task=pre_launch_task,
            workspace_folder=workspace_folder,
            raw=raw,
        ))

    raw_compounds = data.get('compounds', [])
    compounds = []
    for raw in raw_compounds:
        compounds.append(CompoundLaunch(
            name=raw.get('name', ''),
            configurations=[str(c) for c in raw.get('configurations', [])],
            workspace_folder=workspace_folder,
            raw=raw,
        ))

    return WorkspaceLaunch(
        configs=configs,
        compounds=compounds,
        inputs=inputs,
        workspace_folder=workspace_folder,
    )
