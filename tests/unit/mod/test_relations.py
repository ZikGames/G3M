"""Tests for operation dependency and conflict profile analysis."""

from __future__ import annotations

from utils.mod.relations import analyze_mod_relations, recommend_mod_arrangement


def _config(**relations: list[str]) -> dict[str, object]:
    return {
        "config_version": "2.0.0",
        "id": "placeholder",
        "name": "Placeholder",
        "version": "1.0.0",
        "authors": [],
        "game": "deltarune",
        "files": [],
        **relations,
    }


def test_relations_reports_missing_and_inactive_dependencies():
    configs = {
        "main": _config(dependencies=["missing", "inactive:before"]),
        "inactive": _config(),
    }

    findings = analyze_mod_relations(configs, [["main"]])

    assert [(finding.code, finding.related_id) for finding in findings] == [
        ("dependency_missing", "missing"),
        ("dependency_inactive", "inactive"),
    ]


def test_relations_checks_each_mode_against_profile_steps():
    configs = {
        "main": _config(
            dependencies=["earlier:before-step", "lower:before-priority"],
            conflicts=["higher:after-priority"],
        ),
        "earlier": _config(),
        "higher": _config(),
        "lower": _config(),
    }

    findings = analyze_mod_relations(configs, [["earlier"], ["higher", "main", "lower"]])

    assert [(finding.code, finding.related_id) for finding in findings] == [
        ("conflict_active", "higher"),
    ]
    assert findings[0].severity == "warning"


def test_relations_reports_unsatisfied_relation_and_cycles():
    configs = {
        "a": _config(dependencies=["b:before"]),
        "b": _config(dependencies=["a:before"]),
    }

    findings = analyze_mod_relations(configs, [["a", "b"]])

    assert {finding.code for finding in findings} == {
        "dependency_relation_unsatisfied",
        "dependency_cycle",
    }
    assert {finding.mod_id for finding in findings if finding.code == "dependency_cycle"} == {
        "a",
        "b",
    }


def test_arrangement_stably_satisfies_ordinary_dependency_order():
    configs = {
        "main": _config(dependencies=["base:before"]),
        "base": _config(),
        "unrelated": _config(),
    }

    arrangement = recommend_mod_arrangement(configs, [["main", "unrelated"], ["base"]])

    assert arrangement.feasible
    assert arrangement.steps == (("unrelated", "base"), ("main",))
    assert arrangement.findings == ()


def test_arrangement_moves_dependency_to_required_step_and_priority():
    configs = {
        "main": _config(
            dependencies=["base:before-step", "priority:before-priority"]
        ),
        "base": _config(),
        "priority": _config(),
    }

    arrangement = recommend_mod_arrangement(configs, [["priority", "main"], ["base"]])

    assert arrangement.feasible
    assert arrangement.steps == (("base",), ("main", "priority"))


def test_arrangement_keeps_profile_when_a_conflict_would_be_active():
    configs = {
        "main": _config(dependencies=["base:before"], conflicts=["other"]),
        "base": _config(),
        "other": _config(),
    }
    original = [["main", "other", "base"]]

    arrangement = recommend_mod_arrangement(configs, original)

    assert not arrangement.feasible
    assert arrangement.steps == (("main", "other", "base"),)
    assert {finding.code for finding in arrangement.findings} == {"conflict_active"}


def test_arrangement_moves_mods_out_of_a_bad_conflict_order():
    configs = {
        "main": _config(conflicts=["other:before"]),
        "other": _config(),
    }

    arrangement = recommend_mod_arrangement(configs, [["other", "main"]])

    assert arrangement.feasible
    assert arrangement.steps == (("main", "other"),)
    assert arrangement.findings == ()
