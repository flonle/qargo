# vsctasks

A Unix-style CLI for scanning, listing, and running `.vscode/tasks.json` tasks
and `.vscode/launch.json` configs across a directory tree.

## Install

Requires Python 3.11+.

```sh
uv tool install .
# or, from a checkout:
pip install .
```

## Usage

```sh
vsctasks list                     # list every task / launch / compound
vsctasks run '[my-repo] Build'    # run one by ID
vsctasks info '[my-repo] Build'   # print the raw JSON definition
```

IDs look like:

- `[workspace] Label` — a task from `tasks.json`
- `[workspace] >Name` — a launch config from `launch.json`
- `[workspace] >+Name` — a compound launch (children run in parallel,
  output is interleaved and prefixed)

`run` accepts multiple IDs, or reads one ID per line from stdin — so it
composes naturally with `fzf`, which is the recommended mode of interaction.

I recommend putting this in your `~/.whateverrc`:

```sh
function vt () {
    local root="${1:-.}"
    local task
    task=$(vsctasks list --root "$root" \
| fzf --delimiter $'\t' --with-nth 1 --preview 'vsctasks info {2} | jq -C' --preview-window=right:40% \
| cut -f2)
    [[ -z "$task" ]] && return
    print -s "vsctasks run --root ${(q)root} '${(q)task}'"
    vsctasks run --root "$root" "$task"
}
```

It streams all items from `vsctasks list` to `fzf`, as they're found. A preview is shown
in `fzf` for each item. When selected, it is executed, and added to the shell's history.
That way, you can just hit ↑ to try again.

## Discovery

Walks from `--root` (default `.`), descending into any `.vscode/` it finds.
Skips hidden dirs, common build/output dirs (`node_modules`, `dist`, `target`,
…), and any directory containing a `.vsctasksignore` file. Extra names can be
pruned with repeated `--exclude PATTERN`.
