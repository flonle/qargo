"""Dependency resolution: dependsOn graph and topological sort."""

from __future__ import annotations

from collections import defaultdict, deque

from .parse import Task


def topological_sort(task: Task, all_tasks: dict[str, Task]) -> list[Task]:
    """Return a linear execution order for *task* and all its transitive dependencies.

    Uses Kahn's algorithm. Raises ValueError on cycles.
    The requested task itself is last in the list.
    """
    # Collect all reachable tasks via BFS
    reachable: dict[str, Task] = {}
    queue: deque[str] = deque([task.label])
    while queue:
        label = queue.popleft()
        if label in reachable:
            continue
        t = all_tasks.get(label)
        if t is None:
            raise ValueError(f"Task '{label}' referenced in dependsOn but not found")
        reachable[label] = t
        for dep in t.depends_on:
            if dep not in reachable:
                queue.append(dep)

    # Build in-degree map and adjacency list within reachable set
    # Edge: dep -> task (dep must come before task)
    in_degree: dict[str, int] = {lbl: 0 for lbl in reachable}
    successors: dict[str, list[str]] = defaultdict(list)

    for lbl, t in reachable.items():
        for dep in t.depends_on:
            if dep in reachable:
                successors[dep].append(lbl)
                in_degree[lbl] += 1

    ready = deque(lbl for lbl, deg in in_degree.items() if deg == 0)
    order: list[Task] = []

    while ready:
        lbl = ready.popleft()
        order.append(reachable[lbl])
        for successor in successors[lbl]:
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                ready.append(successor)

    if len(order) != len(reachable):
        cycle_nodes = [lbl for lbl, deg in in_degree.items() if deg > 0]
        raise ValueError(f"Cycle detected in dependsOn graph involving: {cycle_nodes}")

    return order
