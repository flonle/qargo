import re
import subprocess
import sys
import tempfile
from pathlib import Path

import click

MASTER = Path.home() / "dev" / "qargo"
WORKSPACES_DIR = Path.home() / ".qargo-workspaces"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Directories that are large/generated — symlinked from master instead of copied
HEAVY_DIRS = [
    "node_modules",
    ".venv",
    ".mypy_cache",
    "__pycache__",
    ".next",
    "dist",
    "build",
]


def find_sub_repos(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / ".git").exists())


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def git_branch_exists(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo, capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def sync_untracked(master_repo: Path, workspace_repo: Path, *, symlink_heavy: bool) -> None:
    """Rsync untracked (and gitignored) files from master_repo into workspace_repo."""
    # Get list of git-tracked files to exclude from rsync
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=master_repo, capture_output=True, text=True, check=True,
    )
    tracked_files = result.stdout

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(tracked_files)
        tracked_list = Path(f.name)

    try:
        rsync_cmd = [
            "rsync", "-a", "--exclude=.git/",
            "--exclude-from", str(tracked_list),
        ]

        if symlink_heavy:
            for d in HEAVY_DIRS:
                rsync_cmd += ["--exclude", f"{d}/"]
        else:
            for d in HEAVY_DIRS:
                rsync_cmd += ["--exclude", f"{d}/"]

        rsync_cmd += [f"{master_repo}/", str(workspace_repo)]
        subprocess.run(rsync_cmd, check=True)
    finally:
        tracked_list.unlink(missing_ok=True)

    if symlink_heavy:
        for d in HEAVY_DIRS:
            source = master_repo / d
            dest = workspace_repo / d
            if source.exists() and not dest.exists():
                dest.symlink_to(source)
                click.echo(f"  Symlinked {dest.name}/ → master")


def sync_root_files(workspace: Path, sub_repos: list[Path]) -> None:
    """Rsync root-level non-git files from master into workspace."""
    rsync_cmd = ["rsync", "-a"]
    for repo in sub_repos:
        rsync_cmd += ["--exclude", f"{repo.name}/"]
    for d in HEAVY_DIRS:
        rsync_cmd += ["--exclude", f"{d}/"]
    rsync_cmd += [f"{MASTER}/", str(workspace)]
    subprocess.run(rsync_cmd, check=True)


def _do_sync(workspace: Path, symlink_heavy: bool) -> None:
    sub_repos = find_sub_repos(MASTER)
    for repo in sub_repos:
        worktree = workspace / repo.name
        if not worktree.exists():
            click.echo(f"  Skipping {repo.name}/ (worktree not found)", err=True)
            continue
        click.echo(f"  Syncing untracked files: {repo.name}/")
        sync_untracked(repo, worktree, symlink_heavy=symlink_heavy)
    click.echo("  Syncing root-level files")
    sync_root_files(workspace, sub_repos)


@click.group()
def cli() -> None:
    """Manage parallel qargo workspaces backed by git worktrees."""


@cli.command()
@click.argument("name")
@click.option("--no-symlink-heavy", is_flag=True, default=False,
               help="Copy heavy dirs (node_modules etc.) instead of symlinking them.")
def create(name: str, no_symlink_heavy: bool) -> None:
    """Create a new workspace NAME with git worktrees for each sub-repo."""
    workspace = WORKSPACES_DIR / name
    if workspace.exists():
        click.echo(f"Error: workspace '{name}' already exists at {workspace}", err=True)
        sys.exit(1)

    sub_repos = find_sub_repos(MASTER)
    branch = f"workspace/{name}"

    # Check for branch conflicts upfront
    for repo in sub_repos:
        if git_branch_exists(repo, branch):
            click.echo(
                f"Error: branch '{branch}' already exists in {repo.name}/. "
                "Remove it or choose a different name.",
                err=True,
            )
            sys.exit(1)

    workspace.mkdir(parents=True)
    click.echo(f"Creating workspace '{name}' at {workspace}")

    for repo in sub_repos:
        worktree = workspace / repo.name
        click.echo(f"  Adding worktree: {repo.name}/ (branch: {branch})")
        git("worktree", "add", str(worktree), "-b", branch, cwd=repo)

    click.echo("Syncing untracked files...")
    _do_sync(workspace, symlink_heavy=not no_symlink_heavy)
    click.echo(f"\nWorkspace '{name}' ready at {workspace}")
    click.echo(f"Each sub-repo is on branch '{branch}' sharing the master git database.")


@cli.command()
@click.argument("name")
@click.option("--force", is_flag=True, default=False,
               help="Force removal even if the worktree has uncommitted changes.")
def remove(name: str, force: bool) -> None:
    """Remove workspace NAME and deregister its git worktrees."""
    workspace = WORKSPACES_DIR / name
    if not workspace.exists():
        click.echo(f"Error: workspace '{name}' not found at {workspace}", err=True)
        sys.exit(1)

    sub_repos = find_sub_repos(MASTER)
    click.echo(f"Removing workspace '{name}'...")

    for repo in sub_repos:
        worktree = workspace / repo.name
        if not worktree.exists():
            continue
        click.echo(f"  Removing worktree: {repo.name}/")
        cmd = ["git", "worktree", "remove", str(worktree)]
        if force:
            cmd.append("--force")
        result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        if result.returncode != 0:
            click.echo(f"  Warning: {result.stderr.strip()}", err=True)
            click.echo("  Tip: use --force to remove worktrees with uncommitted changes.", err=True)
            sys.exit(1)

    # Remove any remaining files (root-level synced files, stale dirs)
    import shutil
    if workspace.exists():
        shutil.rmtree(workspace)

    click.echo(f"Workspace '{name}' removed.")


@cli.command()
@click.argument("name", required=False)
@click.option("--no-symlink-heavy", is_flag=True, default=False,
               help="Do not create new symlinks for heavy dirs.")
def sync(name: str | None, no_symlink_heavy: bool) -> None:
    """Sync untracked files from master to workspace(s).

    If NAME is omitted, syncs all workspaces.
    """
    if name:
        workspace = WORKSPACES_DIR / name
        if not workspace.exists():
            click.echo(f"Error: workspace '{name}' not found at {workspace}", err=True)
            sys.exit(1)
        workspaces = [workspace]
    else:
        if not WORKSPACES_DIR.exists():
            click.echo("No workspaces found.", err=True)
            sys.exit(0)
        workspaces = sorted(p for p in WORKSPACES_DIR.iterdir() if p.is_dir())
        if not workspaces:
            click.echo("No workspaces found.")
            return

    for ws in workspaces:
        click.echo(f"Syncing workspace '{ws.name}'...")
        _do_sync(ws, symlink_heavy=not no_symlink_heavy)

    click.echo("Done.")


def _color_status_line(line: str) -> str:
    """Color a git status --short line like git does."""
    if len(line) < 2:
        return line
    index = line[0]   # staged status
    worktree = line[1]  # unstaged status
    rest = line[2:]

    # Staged changes (index column) → green
    if index in "MADRC":
        index = click.style(index, fg="green")
    # Unstaged changes (worktree column) → red
    if worktree in "MD":
        worktree = click.style(worktree, fg="red")
    # Untracked → red
    if line.startswith("??"):
        return click.style(line, fg="red")
    # Unmerged → red
    if line[0] in "UDA" and line[1] in "UDA":
        return click.style(line, fg="red")

    return index + worktree + rest


def _detect_workspace() -> tuple[str, Path] | None:
    """If cwd is inside master or a workspace, return (label, root)."""
    cwd = Path.cwd().resolve()
    master_resolved = MASTER.resolve()
    if cwd == master_resolved or master_resolved in cwd.parents:
        return ("master", MASTER)
    if WORKSPACES_DIR.exists():
        ws_resolved = WORKSPACES_DIR.resolve()
        if cwd == ws_resolved or ws_resolved in cwd.parents:
            # Extract workspace name (first component after WORKSPACES_DIR)
            relative = cwd.relative_to(ws_resolved)
            ws_name = relative.parts[0] if relative.parts else None
            if ws_name and (WORKSPACES_DIR / ws_name).is_dir():
                return (ws_name, WORKSPACES_DIR / ws_name)
    return None


def _count_claude_sessions(path: Path) -> int:
    """Count Claude session .jsonl files linked to PATH.

    Claude Code encodes cwd as the project dir name by replacing any
    non-alphanumeric character (except `-`) with `-`.
    """
    encoded = re.sub(r"[^a-zA-Z0-9-]", "-", str(path.resolve()))
    project_dir = CLAUDE_PROJECTS_DIR / encoded
    if not project_dir.is_dir():
        return 0
    return sum(1 for p in project_dir.iterdir() if p.is_file() and p.suffix == ".jsonl")


@cli.command()
def status() -> None:
    """Show git status for sub-repos. Auto-detects workspace from cwd."""
    detected = _detect_workspace()
    if detected:
        roots = [detected]
    else:
        roots: list[tuple[str, Path]] = [("master", MASTER)]
        if WORKSPACES_DIR.exists():
            for ws in sorted(WORKSPACES_DIR.iterdir()):
                if ws.is_dir():
                    roots.append((ws.name, ws))

    for label, root in roots:
        display_path = str(root).replace(str(Path.home()), "~", 1)
        path_suffix = "  " + click.style(display_path, dim=True) if len(roots) > 1 else ""
        session_count = _count_claude_sessions(root)
        session_suffix = (
            "  " + click.style(f"✳ {session_count}", fg="magenta")
            if session_count
            else ""
        )
        click.echo(click.style(f"[{label}]", bold=True) + path_suffix + session_suffix)
        for repo in find_sub_repos(root):
            result = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=repo, capture_output=True, text=True,
            )
            lines = result.stdout.splitlines()
            branch_line = lines[0] if lines else ""
            changes = lines[1:]
            suffix = click.style(f"  ({len(changes)} changed)", fg="yellow") if changes else ""
            branch_display = branch_line.replace("## ", "⎇  ", 1)
            branch_colored = click.style(branch_display, fg="cyan")
            click.echo(f"  ├─ {repo.name + '/':<15} {branch_colored}{suffix}")
            for line in changes:
                click.echo(f"  │     {_color_status_line(line)}")
        click.echo()


@cli.command("list")
def list_workspaces() -> None:
    """List all workspaces."""
    if not WORKSPACES_DIR.exists():
        click.echo("No workspaces directory found.")
        return
    workspaces = sorted(p for p in WORKSPACES_DIR.iterdir() if p.is_dir())
    if not workspaces:
        click.echo("No workspaces.")
        return
    for ws in workspaces:
        click.echo(str(ws))
