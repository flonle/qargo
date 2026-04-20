# qargo-workspaces

A small CLI (`qws`) for running multiple parallel copies of the `qargo` monorepo,
each on its own branch, backed by git worktrees.

## Why

The `qargo` repo is a meta-repo composed of several sub-repos (`backend`,
`frontend`, `dbt`, `mobile-app`, ...). Switching branches across all of them to
work on a second task is slow and clobbers untracked state. `qws` spins up a
dedicated directory per task with one worktree per sub-repo, all on a shared
branch name, and keeps untracked files rsynced in.

A main motivator: isolated directories for parallel Claude Code sessions that
don't step on each other.

Heavy, regenerable directories (`node_modules`, `.venv`, `.mypy_cache`, ...) are
symlinked back to master rather than copied, so workspaces are cheap to create.

## Layout

- Master clone: `~/dev/qargo`
- Workspaces: `~/.qargo-workspaces/<name>/`

> The tool is hard-coded to the paths above; you might want to change them.

## Install

```bash
uv tool install .
```

Or run from source: `uv run qws ...`

## Usage

See `qws --help`

## Shell integration

`qws` doesn't ship a `switch` / `cd` command because a shell function does the
job in three lines. Drop this into your `.zshrc` / `.bashrc` to fzf-pick a
workspace and `cd` into it:

```sh
workon () {
    local dir="$HOME/.qargo-workspaces/$(ls ~/.qargo-workspaces | fzf)"
    print -s "cd $dir"
    cd "$dir"
}
```

> The `print -s` is to add the `cd` command to your shell history so that you can ↑ to it later.

## Notes

- Branch names clash on `create` if `workspace/<name>` already exists in any
  sub-repo — the command aborts before touching anything.
- `remove` uses `git worktree remove`, so it will refuse to drop a worktree
  with uncommitted changes unless `--force` is passed.
