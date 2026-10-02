"""Unit tests for test warning preferences."""

from services.mod_operation_support import direct_operation_paths_preapproved
from services.warning_service import (
    WarningSeverity,
    create_warning_event,
    get_warning_definition,
    is_warning_enabled,
    normalize_warning_preferences,
)


def test_skip_patching_warnings_migrates_to_skip_all():
    config = {"skip_patching_warnings": True}

    prefs = normalize_warning_preferences(config)

    assert prefs["skip_all"] is True
    assert is_warning_enabled("xdelta_apply_failed", config) is False


def test_legacy_section_override_is_removed_and_ignored():
    config = {
        "warning_preferences": {
            "skip_all": False,
            "section_overrides": {"major": False},
            "warning_overrides": {"xdelta_apply_failed": False},
        }
    }
    prefs = normalize_warning_preferences(config)

    assert "section_overrides" not in prefs
    assert is_warning_enabled("xdelta_apply_failed", config) is False


def test_individual_warning_override_uses_registry_defaults():
    config = {
        "warning_preferences": {
            "warning_overrides": {
                "patching_warning": False,
            }
        }
    }

    assert is_warning_enabled("patching_warning", config) is False


def test_direct_absolute_paths_require_their_own_explicit_approval():
    assert not direct_operation_paths_preapproved(
        {"warning_preferences": {"skip_all": True}}
    )
    assert direct_operation_paths_preapproved(
        {
            "warning_preferences": {
                "skip_all": True,
                "warning_overrides": {"direct_absolute_operation_paths": False},
            }
        }
    )


def test_warning_event_keeps_severity_and_context():
    definition = get_warning_definition("xdelta_apply_failed")

    event = create_warning_event(
        "xdelta_apply_failed",
        context={"patch_name": "mod.xdelta", "reason": "checksum mismatch"},
    )

    assert definition.severity is WarningSeverity.CRITICAL
    assert event.warning_id == "xdelta_apply_failed"
    assert event.context["patch_name"] == "mod.xdelta"


def test_unknown_warning_event_logs_fallback(caplog):
    event = create_warning_event(
        "unknown_warning",
        context={"patch_name": "mod.xdelta"},
    )

    assert event.warning_id == "patching_warning"
    assert "unknown_warning" in caplog.text
    assert "patching_warning" in caplog.text
