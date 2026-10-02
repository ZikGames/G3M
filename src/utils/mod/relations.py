"""Profile-scoped dependency and conflict analysis for mod config operation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from heapq import heapify, heappop, heappush

from utils.mod.config import MOD_CONFIG_RELATION_MODES


@dataclass(frozen=True, slots=True)
class ModRelation:
    """One compact ``id`` or ``id:mode`` relation entry."""

    mod_id: str
    mode: str | None = None


@dataclass(frozen=True, slots=True)
class RelationFinding:
    """One accurate profile-dependent relation result."""

    severity: str
    code: str
    mod_id: str
    related_id: str
    mode: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RelationArrangement:
    """One safe, deterministic candidate for the active profile rows."""

    steps: tuple[tuple[str, ...], ...]
    feasible: bool
    findings: tuple[RelationFinding, ...]


def parse_mod_relation(value: str) -> ModRelation:
    """Parse a relation already accepted by the strict operation config validator."""
    mod_id, separator, mode = value.partition(":")
    if not separator:
        return ModRelation(mod_id)
    if mode not in MOD_CONFIG_RELATION_MODES:
        raise ValueError(f"unsupported relationship mode: {mode}")
    return ModRelation(mod_id, mode)


def _relations(config: Mapping[str, object], field: str) -> tuple[ModRelation, ...]:
    entries = config.get(field)
    if not isinstance(entries, list):
        return ()
    parsed: list[ModRelation] = []
    for entry in entries:
        if isinstance(entry, str):
            try:
                parsed.append(parse_mod_relation(entry))
            except ValueError:
                continue
    return tuple(parsed)


def _positions(
    steps: Sequence[Sequence[str]],
) -> tuple[dict[str, tuple[int, int]], tuple[RelationFinding, ...]]:
    positions: dict[str, tuple[int, int]] = {}
    findings: list[RelationFinding] = []
    for step_index, step in enumerate(steps):
        for priority_index, mod_id in enumerate(step):
            if mod_id in positions:
                findings.append(
                    RelationFinding(
                        "error",
                        "profile_duplicate_mod",
                        mod_id,
                        mod_id,
                        None,
                        "The profile contains this mod more than once.",
                    )
                )
                continue
            positions[mod_id] = (step_index, priority_index)
    return positions, tuple(findings)


def _satisfies(
    current: tuple[int, int], related: tuple[int, int], mode: str | None
) -> bool:
    if mode is None:
        return True
    current_step, current_priority = current
    related_step, related_priority = related
    if mode == "before":
        return related < current
    if mode == "after":
        return related > current
    if mode == "before-step":
        return related_step < current_step
    if mode == "after-step":
        return related_step > current_step
    if mode == "before-priority":
        return related_step == current_step and related_priority > current_priority
    return related_step == current_step and related_priority < current_priority


def _edge(
    current_id: str, relation: ModRelation
) -> tuple[str, str] | None:
    if relation.mode is None:
        return None
    if relation.mode in {"before", "before-step", "after-priority"}:
        return relation.mod_id, current_id
    return current_id, relation.mod_id


def _conflict_avoidance_edge(
    current_id: str, relation: ModRelation
) -> tuple[str, str] | None:
    """Return the ordinary ordering edge that avoids one bad conflict order."""
    if relation.mode == "before":
        return current_id, relation.mod_id
    if relation.mode == "after":
        return relation.mod_id, current_id
    return None


def _cycles(edges: Sequence[tuple[str, str]]) -> set[str]:
    graph: dict[str, list[str]] = {}
    for source, target in edges:
        graph.setdefault(source, []).append(target)

    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            cyclic.update(trail[trail.index(node) :])
            return
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, []):
            visit(child, [*trail, child])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [node])
    return cyclic


def _stable_topological_order(
    ordered_ids: Sequence[str], edges: Sequence[tuple[str, str]]
) -> tuple[str, ...] | None:
    """Return a stable dependency order, or ``None`` for a cycle."""
    positions = {mod_id: index for index, mod_id in enumerate(ordered_ids)}
    outgoing: dict[str, list[str]] = {mod_id: [] for mod_id in ordered_ids}
    indegree = dict.fromkeys(ordered_ids, 0)
    for source, target in edges:
        if source not in positions or target not in positions or target in outgoing[source]:
            continue
        outgoing[source].append(target)
        indegree[target] += 1

    ready = [(positions[mod_id], mod_id) for mod_id in ordered_ids if indegree[mod_id] == 0]
    heapify(ready)
    result: list[str] = []
    while ready:
        _, mod_id = heappop(ready)
        result.append(mod_id)
        for dependent in outgoing[mod_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heappush(ready, (positions[dependent], dependent))
    return tuple(result) if len(result) == len(ordered_ids) else None


def _move_before_or_after(
    steps: list[list[str]], current_id: str, related_id: str, *, after: bool
) -> None:
    for step in steps:
        if related_id in step:
            step.remove(related_id)
            break
    for step in steps:
        if current_id in step:
            index = step.index(current_id) + int(after)
            step.insert(index, related_id)
            return


def _move_to_adjacent_step(
    steps: list[list[str]], current_id: str, related_id: str, *, after: bool
) -> None:
    for step in steps:
        if related_id in step:
            step.remove(related_id)
            break
    current_step = next(index for index, step in enumerate(steps) if current_id in step)
    if after:
        target_step = current_step + 1
        if target_step == len(steps):
            steps.append([])
    else:
        if current_step == 0:
            steps.insert(0, [])
            current_step += 1
        target_step = current_step - 1
    steps[target_step].append(related_id)


def _apply_step_and_priority_relations(
    configs: Mapping[str, Mapping[str, object]], steps: list[list[str]]
) -> None:
    for step in tuple(steps):
        for mod_id in tuple(step):
            config = configs.get(mod_id)
            if config is None:
                continue
            for relation in _relations(config, "dependencies"):
                if relation.mode is None or not any(relation.mod_id in row for row in steps):
                    continue
                positions, _ = _positions(steps)
                if _satisfies(positions[mod_id], positions[relation.mod_id], relation.mode):
                    continue
                if relation.mode == "before-step":
                    _move_to_adjacent_step(steps, mod_id, relation.mod_id, after=False)
                elif relation.mode == "after-step":
                    _move_to_adjacent_step(steps, mod_id, relation.mod_id, after=True)
                elif relation.mode == "before-priority":
                    _move_before_or_after(steps, mod_id, relation.mod_id, after=True)
                elif relation.mode == "after-priority":
                    _move_before_or_after(steps, mod_id, relation.mod_id, after=False)
            for relation in _relations(config, "conflicts"):
                if not any(relation.mod_id in row for row in steps):
                    continue
                positions, _ = _positions(steps)
                if not _satisfies(positions[mod_id], positions[relation.mod_id], relation.mode):
                    continue
                if relation.mode == "before-step":
                    _move_to_adjacent_step(steps, mod_id, relation.mod_id, after=True)
                elif relation.mode == "after-step":
                    _move_to_adjacent_step(steps, mod_id, relation.mod_id, after=False)
                elif relation.mode == "before-priority":
                    _move_before_or_after(steps, mod_id, relation.mod_id, after=False)
                elif relation.mode == "after-priority":
                    _move_before_or_after(steps, mod_id, relation.mod_id, after=True)


def recommend_mod_arrangement(
    configs: Mapping[str, Mapping[str, object]], steps: Sequence[Sequence[str]]
) -> RelationArrangement:
    """Build one stable recommendation without mutating the profile.

    Missing dependencies and every unsatisfied or unsafe final relation leave the
    original layout intact. The caller can present those findings without
    claiming that an arrangement was applied.
    """
    original = tuple(tuple(step) for step in steps if step)
    initial_findings = analyze_mod_relations(configs, original)
    blocked_codes = {
        "dependency_missing",
        "dependency_inactive",
        "dependency_cycle",
        "profile_duplicate_mod",
    }
    if any(finding.code in blocked_codes for finding in initial_findings):
        return RelationArrangement(original, False, initial_findings)

    ordered_ids = tuple(mod_id for step in original for mod_id in step)
    ordinary_edges: list[tuple[str, str]] = []
    for mod_id in ordered_ids:
        config = configs.get(mod_id)
        if config is None:
            continue
        for relation in _relations(config, "dependencies"):
            if relation.mode in {"before", "after"}:
                edge = _edge(mod_id, relation)
                if edge is not None:
                    ordinary_edges.append(edge)
        for relation in _relations(config, "conflicts"):
            edge = _conflict_avoidance_edge(mod_id, relation)
            if edge is not None:
                ordinary_edges.append(edge)
    ordered = _stable_topological_order(ordered_ids, ordinary_edges)
    if ordered is None:
        return RelationArrangement(original, False, initial_findings)

    result: list[list[str]] = []
    cursor = 0
    for step in original:
        count = len(step)
        result.append(list(ordered[cursor : cursor + count]))
        cursor += count
    _apply_step_and_priority_relations(configs, result)
    candidate = tuple(tuple(step) for step in result if step)
    findings = analyze_mod_relations(configs, candidate)
    unsafe_codes = {
        "dependency_relation_unsatisfied",
        "dependency_cycle",
        "profile_duplicate_mod",
        "conflict_active",
    }
    if any(finding.code in unsafe_codes for finding in findings):
        return RelationArrangement(original, False, findings)
    return RelationArrangement(candidate, candidate != original, findings)


def analyze_mod_relations(
    configs: Mapping[str, Mapping[str, object]], steps: Sequence[Sequence[str]]
) -> tuple[RelationFinding, ...]:
    """Evaluate operation relations against one profile's current ordered steps.

    The profile's rows are the only source of current placement. This function
    neither moves mods nor guesses a possible arrangement, so diagnostics and
    launch always report the same observed state.
    """
    positions, duplicate_findings = _positions(steps)
    findings = list(duplicate_findings)
    dependency_edges: list[tuple[str, str]] = []

    for mod_id in positions:
        config = configs.get(mod_id)
        if config is None:
            continue
        current_position = positions[mod_id]
        for relation in _relations(config, "dependencies"):
            related_position = positions.get(relation.mod_id)
            if relation.mod_id not in configs:
                findings.append(
                    RelationFinding(
                        "warning",
                        "dependency_missing",
                        mod_id,
                        relation.mod_id,
                        relation.mode,
                        "The dependency is not installed in the library.",
                    )
                )
                continue
            if related_position is None:
                findings.append(
                    RelationFinding(
                        "warning",
                        "dependency_inactive",
                        mod_id,
                        relation.mod_id,
                        relation.mode,
                        "The dependency is installed but inactive in this profile.",
                    )
                )
                continue
            if not _satisfies(current_position, related_position, relation.mode):
                findings.append(
                    RelationFinding(
                        "warning",
                        "dependency_relation_unsatisfied",
                        mod_id,
                        relation.mod_id,
                        relation.mode,
                        "The dependency is active but its requested relation is not met.",
                    )
                )
            edge = _edge(mod_id, relation)
            if edge:
                dependency_edges.append(edge)

        for relation in _relations(config, "conflicts"):
            related_position = positions.get(relation.mod_id)
            if related_position is None:
                continue
            if _satisfies(current_position, related_position, relation.mode):
                findings.append(
                    RelationFinding(
                        "warning",
                        "conflict_active",
                        mod_id,
                        relation.mod_id,
                        relation.mode,
                        "The active profile matches a declared conflict.",
                    )
                )

    for mod_id in sorted(_cycles(dependency_edges)):
        findings.append(
            RelationFinding(
                "error",
                "dependency_cycle",
                mod_id,
                mod_id,
                None,
                "Dependency relations form a cycle in the active profile.",
            )
        )
    return tuple(findings)
