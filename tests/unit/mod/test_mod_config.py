"""Contract tests for the strict mod config operation parser."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from utils.mod.config import (
    MOD_CONFIG_MAX_DISPLAY_CHARS,
    MOD_CONFIG_VERSION,
    ModConfigValidationError,
    config_has_files_for_section,
    iter_mod_config_leaves,
    load_mod_config,
    parse_mod_config,
    portable_user_path,
    read_mod_config_bytes,
    validate_mod_config,
    write_mod_config,
)
from utils.mod.legacy_config_migration import (
    migrate_legacy_config,
    migrate_legacy_config_file,
)
from utils.mod.operation_plan import section_target_root
from utils.mod.utils import resolve_mod_icon


def _valid_config() -> dict[str, object]:
    return {
        "config_version": MOD_CONFIG_VERSION,
        "id": "test_mod",
        "name": "Test Mod",
        "version": "1.0.0",
        "authors": ["Test Author"],
        "game": "deltarune",
        "placeholders": {"saves_path": "${user_path}/AppData/Local/Test"},
        "dependencies": ["gb_mod_base:before-step"],
        "conflicts": ["gb_mod_legacy_ui:before-priority"],
        "files": [
            {
                "source": "${mod_path}/patches/data.xdelta",
                "target": "${game_path}/chapter_1_windows/data.win",
                "type": "patch",
                "source_hash": "sha256:" + "a" * 64,
            },
            {
                "Documentation": [
                    {"source": "${mod_path}/README.md", "type": "info"}
                ]
            },
        ],
    }


def test_parse_mod_config_preserves_canonical_top_level_order():
    config = _valid_config()

    parsed = parse_mod_config(config)

    assert list(parsed) == [
        "config_version",
        "id",
        "name",
        "version",
        "authors",
        "game",
        "placeholders",
        "dependencies",
        "conflicts",
        "files",
    ]
    assert parsed == config
    assert parsed is not config


def test_operation_accepts_mixed_case_custom_placeholder_names():
    config = _valid_config()
    config["placeholders"] = {"AssetFolder": "${mod_path}/assets"}
    config["files"] = [
        {
            "source": "${AssetFolder}/payload.txt",
            "target": "${game_path}/payload.txt",
            "type": "overwrite",
        }
    ]

    assert not validate_mod_config(config)


def test_operation_accepts_an_empty_file_list_for_metadata_only_mods():
    config = _valid_config()
    config["files"] = []

    assert parse_mod_config(config)["files"] == []


@pytest.mark.parametrize("invalid_type", [[], {}, 1, None])
def test_invalid_operation_types_produce_validation_issues(invalid_type):
    config = _valid_config()
    config["files"] = [{"source": "${mod_path}/file.txt", "type": invalid_type}]

    with pytest.raises(ModConfigValidationError, match="supported file operation type"):
        parse_mod_config(config)


@pytest.mark.parametrize("invalid_tag", [{}, [], None])
def test_invalid_tags_produce_validation_issues(invalid_tag):
    config = _valid_config()
    config["tags"] = [invalid_tag]

    with pytest.raises(ModConfigValidationError, match="supported tag"):
        parse_mod_config(config)


@pytest.mark.parametrize("url", ["http://[", "http://example.com:bad", "http://example.com:65536", "http://:80", "http://exa mple.com", "http://example.com/a b"])
def test_malformed_url_produces_a_validation_issue(url):
    config = _valid_config()
    config["homepage"] = url

    with pytest.raises(ModConfigValidationError) as error:
        parse_mod_config(config)
    assert any(issue.code == "invalid_url" for issue in error.value.issues)


def test_deeply_nested_input_produces_a_validation_issue(tmp_path):
    path = tmp_path / "mod_config.json"
    path.write_text('{"files":' + "[" * 2_000 + "]" * 2_000 + "}", encoding="utf-8")

    with pytest.raises(ModConfigValidationError, match="nesting depth"):
        load_mod_config(path)


def test_deeply_nested_in_memory_config_produces_a_validation_issue():
    config = _valid_config()
    nested = []
    for _ in range(2_000):
        nested = [nested]
    config["files"] = nested

    with pytest.raises(ModConfigValidationError, match="nesting depth"):
        parse_mod_config(config)


def test_invalid_utf8_produces_a_validation_issue(tmp_path):
    path = tmp_path / "mod_config.json"
    path.write_bytes(b"\xff")

    with pytest.raises(ModConfigValidationError, match="UTF-8"):
        load_mod_config(path)


def test_config_reader_limits_bytes_read_before_rejecting_large_files(monkeypatch):
    from utils.mod.config import MOD_CONFIG_MAX_BYTES

    source = BytesIO(b"x" * (MOD_CONFIG_MAX_BYTES + 2))
    received = []
    original_read = source.read

    def bounded_read(size):
        received.append(size)
        return original_read(size)

    monkeypatch.setattr(source, "read", bounded_read)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: source)

    with pytest.raises(ModConfigValidationError, match="size limit"):
        read_mod_config_bytes("oversized.json")

    assert received == [MOD_CONFIG_MAX_BYTES + 1]


def test_lone_unicode_surrogates_are_rejected_before_writing(tmp_path):
    config = _valid_config()
    config["name"] = "invalid\ud800"

    with pytest.raises(ModConfigValidationError, match="character"):
        write_mod_config(tmp_path / "mod_config.json", config)


def test_operation_accepts_an_empty_group_while_it_is_being_configured():
    config = _valid_config()
    config["files"] = [{"Future operations": []}]

    assert validate_mod_config(config) == ()


def test_operation_config_rejects_unbounded_file_lists():
    config = _valid_config()
    config["files"] = [
        {"source": f"${{mod_path}}/file_{index}.txt", "type": "info"}
        for index in range(5_001)
    ]

    issues = validate_mod_config(config)

    assert any(issue.code == "too_many" and issue.path == "files" for issue in issues)


def test_operation_leaf_iterator_preserves_depth_first_operation_order():
    entries = [
        {"source": "${mod_path}/first.txt", "type": "info"},
        {
            "Core": [
                {"source": "${mod_path}/second.txt", "type": "info"},
                {
                    "Nested": [
                        {"source": "${mod_path}/third.txt", "type": "info"}
                    ]
                },
            ]
        },
    ]

    assert [
        (path, leaf["source"])
        for path, leaf in iter_mod_config_leaves(entries)
    ] == [
        ((), "${mod_path}/first.txt"),
        (("Core",), "${mod_path}/second.txt"),
        (("Core", "Nested"), "${mod_path}/third.txt"),
    ]


def test_portable_user_path_only_replaces_a_true_home_prefix():
    home = Path.home().as_posix()

    assert portable_user_path(f"{home}/AppData/Local/Game") == "${user_path}/AppData/Local/Game"
    assert portable_user_path(f"{home}-other/AppData") == f"{home}-other/AppData"


def test_operation_resolves_a_mod_local_icon_path_alias(tmp_path):
    config = _valid_config()
    config["placeholders"] = {"assets": "${mod_path}/assets"}
    config["icon"] = "${assets}/icon.png"
    (tmp_path / "assets").mkdir()
    icon = tmp_path / "assets" / "icon.png"
    icon.write_bytes(b"icon")

    assert parse_mod_config(config)["icon"] == "${assets}/icon.png"
    assert resolve_mod_icon(config, str(tmp_path)) == str(icon)


def test_operation_accepts_a_safe_remote_icon_url():
    config = _valid_config()
    config["icon"] = "https://images.example.com/mod.png"

    assert parse_mod_config(config)["icon"] == config["icon"]


def test_migrate_legacy_icon_preserves_remote_and_local_paths(tmp_path):
    icon = tmp_path / "assets" / "icon.png"
    icon.parent.mkdir()
    icon.write_bytes(b"icon")

    local = migrate_legacy_config(
        {"id": "legacy", "name": "Legacy", "game": "deltarune", "files": {}, "icon": str(icon)},
        mod_root_path=tmp_path,
    )
    remote = migrate_legacy_config(
        {"id": "remote", "name": "Remote", "game": "deltarune", "files": {}, "icon_url": "https://example.com/icon.png"},
        mod_root_path=tmp_path,
    )

    assert local["icon"] == "${mod_path}/assets/icon.png"
    assert remote["icon"] == "https://example.com/icon.png"


def test_resolve_mod_icon_does_not_use_legacy_root_fallback(tmp_path):
    (tmp_path / "_icon.png").write_bytes(b"icon")

    assert resolve_mod_icon(_valid_config(), str(tmp_path)) is None


def test_operation_section_detection_uses_targets_and_expands_one_path_alias():
    config = _valid_config()
    config["placeholders"] = {"chapter_one": "${game_path}/chapter1_windows"}
    config["files"] = [
        {
            "source": "${mod_path}/patch.xdelta",
            "target": "${chapter_one}/data.win",
            "type": "patch",
        }
    ]

    assert config_has_files_for_section(config, "deltarune_1")
    assert not config_has_files_for_section(config, "deltarune_2")


def test_operation_section_detection_treats_custom_targets_as_unscoped():
    config = _valid_config()
    config["files"] = [
        {
            "source": "${mod_path}/addon/",
            "target": "${user_path}/AppData/Local/Game/addon/",
            "type": "overwrite",
        }
    ]

    assert config_has_files_for_section(config, "deltarune_1")
    assert config_has_files_for_section(config, "deltarune_5")


def test_section_target_root_scopes_only_deltarune_chapters():
    assert section_target_root("deltarune", "deltarune_1") == "${game_path}/chapter1_windows"
    assert section_target_root("undertale", "undertale") == "${game_path}"


@pytest.mark.parametrize(
    "mode",
    [
        "before",
        "after",
        "before-step",
        "after-step",
        "before-priority",
        "after-priority",
    ],
)
def test_operation_accepts_every_supported_relation_mode(mode: str):
    config = _valid_config()
    config["dependencies"] = [f"gb_mod_base:{mode}"]

    assert validate_mod_config(config) == ()


def test_operation_rejects_unknown_or_contradictory_relations():
    config = _valid_config()
    config["dependencies"] = ["gb_mod_base:before", "gb_mod_base:after"]
    config["conflicts"] = ["test_mod"]

    issues = validate_mod_config(config)

    assert {issue.code for issue in issues} >= {"duplicate_relation", "self_relation"}


def test_operation_rejects_a_mod_that_is_both_dependency_and_conflict():
    config = _valid_config()
    config["conflicts"] = ["gb_mod_base"]

    issues = validate_mod_config(config)

    assert any(issue.code == "contradictory_relation" for issue in issues)


def test_operation_reserves_self_and_exposes_actionable_validation_details():
    config = _valid_config()
    config["id"] = "self"
    config["dependencies"] = ["self:before"]

    issues = validate_mod_config(config)

    assert sum(issue.code == "reserved_id" for issue in issues) == 2
    assert all(issue.severity == "error" and issue.correction for issue in issues)


def test_operation_display_limits_accept_the_editor_boundary():
    config = _valid_config()
    config["name"] = "n" * MOD_CONFIG_MAX_DISPLAY_CHARS

    assert validate_mod_config(config) == ()

    config["name"] += "n"

    assert any(issue.code == "too_long" and issue.path == "name" for issue in validate_mod_config(config))


def test_operation_requires_explicit_paths_and_operation_specific_fields():
    config = _valid_config()
    config["files"] = [
        {"source": "README.md", "target": "${game_path}/README.md", "type": "patch"},
        {
            "source": "${mod_path}/README.md",
            "target": "${game_path}/README.md",
            "type": "info",
        },
    ]

    issues = validate_mod_config(config)

    assert {issue.code for issue in issues} >= {"relative_path", "forbidden_field"}


def test_operation_requires_info_files_to_be_mod_local_documents():
    config = _valid_config()
    config["files"] = [
        {"source": "${user_path}/README.md", "type": "info"},
        {"source": "${mod_path}/cover.png", "type": "info"},
    ]

    issues = validate_mod_config(config)

    assert {issue.code for issue in issues} >= {"info_source", "info_extension"}


def test_operation_rejects_duplicate_group_names_after_nfc_normalization():
    config = _valid_config()
    config["files"] = [
        {"Café": [{"source": "${mod_path}/A.txt", "type": "info"}]},
        {"Cafe\u0301": [{"source": "${mod_path}/B.txt", "type": "info"}]},
    ]

    issues = validate_mod_config(config)

    assert {issue.code for issue in issues} >= {"not_nfc", "duplicate_group"}


def test_load_operation_rejects_duplicate_json_keys(tmp_path):
    config_path = tmp_path / "mod_config.json"
    config_path.write_text(
        json.dumps(_valid_config())[:-1] + ', "name": "Duplicate"}', encoding="utf-8"
    )

    with pytest.raises(ModConfigValidationError) as error:
        load_mod_config(config_path)

    assert error.value.issues[0].code == "duplicate_key"
    assert error.value.issues[0].correction


def test_legacy_metadata_converts_line_endings_and_display_values():
    config = migrate_legacy_config({
        "id": "legacy_mod", "name": "Legacy", "author": "Author", "game": " UNDERtale ",
        "description": "Cafe\u0301\r\nFirst\rSecond", "game_version": "x" * 200, "files": {},
    })

    assert config["id"] == "legacy_mod"
    assert config["game"] == "undertale"
    assert config["description"] == "Caf\u00e9\nFirst\nSecond"
    assert len(config["game_version"]) == MOD_CONFIG_MAX_DISPLAY_CHARS


def test_migrate_legacy_config_converts_data_extra_and_info_operations(tmp_path):
    mod_root = tmp_path / "legacy"
    (mod_root / "chapter_1" / "lang").mkdir(parents=True)
    (mod_root / "chapter_1" / "base.xdelta").write_bytes(b"patch")
    (mod_root / "chapter_1" / "lang" / "en.txt").write_text("text", encoding="utf-8")
    (mod_root / "README.md").write_text("readme", encoding="utf-8")
    (mod_root / "cover.png").write_bytes(b"image")
    legacy = {
        "id": "legacy_mod",
        "name": "Legacy Mod",
        "version": "1.2.3",
        "author": "Legacy Author",
        "game": "deltarune",
        "info_files": {
            "README.md": "show",
            "cover.png": "show",
            "hidden.md": "hide",
        },
        "files": {
            "deltarune_1": {
                "data_file_path": "chapter_1/base.xdelta",
                "extra_files": [
                    {"file_path": "chapter_1/lang/en.txt", "target": "game_folder"},
                    {"file_path": "dependency.bin", "target": "none"},
                ],
            }
        },
    }

    migrated = migrate_legacy_config(legacy, mod_root_path=mod_root)

    assert migrated["authors"] == ["Legacy Author"]
    assert migrated["files"] == [
        {
            "source": "${mod_path}/chapter_1/base.xdelta",
            "target": "${game_path}/chapter1_windows/data.win",
            "type": "patch",
        },
        {
            "source": "${mod_path}/chapter_1/lang/en.txt",
            "target": "${game_path}/chapter1_windows/lang/en.txt",
            "type": "overwrite",
        },
        {"source": "${mod_path}/README.md", "type": "info"},
    ]


def test_migrate_managed_config_file_replaces_only_after_success(tmp_path):
    config_path = tmp_path / "mod_config.json"
    config_path.write_text(
        json.dumps(
            {
                "id": "legacy_mod",
                "name": "Legacy Mod",
                "author": "Legacy Author",
                "game": "undertale",
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    migrated = migrate_legacy_config_file(config_path)

    assert migrated["config_version"] == MOD_CONFIG_VERSION
    assert json.loads(config_path.read_text(encoding="utf-8")) == migrated
    assert list(tmp_path.glob(".mod_config.json.*.tmp")) == []


@pytest.mark.parametrize("suffix", [".xdelta", ".vcdiff", ".g3mpatch", ".csx"])
def test_migrate_legacy_extra_patch_keeps_patch_behavior(tmp_path, suffix):
    mod_root = tmp_path / "legacy"
    patch_path = mod_root / "chapter_1" / f"sprites.png{suffix}"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_bytes(b"patch")
    legacy = {
        "id": "legacy_mod",
        "name": "Legacy Mod",
        "author": "Legacy Author",
        "game": "deltarune",
        "files": {
            "deltarune_1": {
                "extra_files": [
                    {"file_path": f"chapter_1/sprites.png{suffix}", "target": "game_folder"}
                ]
            }
        },
    }

    migrated = migrate_legacy_config(legacy, mod_root_path=mod_root)

    assert migrated["files"] == [
        {
            "source": f"${{mod_path}}/chapter_1/sprites.png{suffix}",
            "target": "${game_path}/chapter1_windows/sprites.png",
            "type": "patch",
        }
    ]


def test_writer_requires_current_config_after_explicit_migration(tmp_path):
    config_path = tmp_path / "mod_config.json"
    (tmp_path / "patch.xdelta").write_bytes(b"patch")

    legacy = {
        "id": "legacy_mod",
        "name": "Legacy Mod",
        "author": "Author",
        "game": "deltarune",
        "files": {"deltarune_0": {"data_file_path": "patch.xdelta"}},
    }
    with pytest.raises(ModConfigValidationError):
        write_mod_config(config_path, legacy)

    written = write_mod_config(
        config_path,
        migrate_legacy_config(legacy, mod_root_path=tmp_path),
        indent=4,
    )

    assert written["config_version"] == MOD_CONFIG_VERSION
    assert json.loads(config_path.read_text(encoding="utf-8")) == written
    assert list(tmp_path.glob(".mod_config.json.*.tmp")) == []


def test_operation_rejects_lzma_member_paths_and_read_only_archive_targets():
    lzma_member = _valid_config()
    lzma_member["files"] = [
        {
            "source": "${mod_path}/payload.lzma/member",
            "target": "${game_path}/target.txt",
            "type": "overwrite",
        }
    ]
    rar_target = _valid_config()
    rar_target["files"] = [
        {
            "source": "${mod_path}/payload.txt",
            "target": "${game_path}/target.rar/member.txt",
            "type": "overwrite",
        }
    ]

    assert validate_mod_config(lzma_member)[0].code == "invalid_archive_path"
    assert validate_mod_config(rar_target)[0].code == "target_archive_write"


def test_operation_requires_operation_specific_file_and_directory_markers():
    hard_overwrite = _valid_config()
    hard_overwrite["files"] = [
        {
            "source": "${mod_path}/payload.txt",
            "target": "${game_path}/target.txt",
            "type": "hard-overwrite",
        }
    ]
    patch_directory = _valid_config()
    patch_directory["files"] = [
        {
            "source": "${mod_path}/patch/",
            "target": "${game_path}/target.txt",
            "type": "patch",
        }
    ]

    assert not validate_mod_config(hard_overwrite)
    assert validate_mod_config(patch_directory)[0].code == "source_kind"
