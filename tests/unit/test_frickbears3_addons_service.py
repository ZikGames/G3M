"""Unit tests for FRICKBEARS3 addon detection, conversion, and apply flows."""

import json
from pathlib import Path

from services.frickbears3_addons_service import Frickbears3AddonsService


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_guard_layout(root: Path, *, full_name: str = "Guard Test") -> None:
    _write_text(
        root / "extras_info.txt",
        json.dumps({"FULL_NAME": full_name, "DESCRIPTION": "Guard description"}),
    )
    _write_text(root / "opening_dialogue.txt", '{"DIALOGUE":["hello"]}')
    for file_name in (
        "icon.png",
        "portrait.png",
        "selection.png",
        "reflection.png",
    ):
        _write_bytes(root / file_name)


def test_frickbears3_service_detects_single_root_addon_layout(tmp_path):
    service = Frickbears3AddonsService()
    extract_dir = tmp_path / "extract"
    guard_root = extract_dir / "Goomba"
    _write_guard_layout(guard_root, full_name="Goomba")

    inspection = service.inspect_extracted_archive(str(extract_dir))

    assert inspection.eligible is True
    assert inspection.layout == "single_root"
    assert inspection.guard_root_dirs == [str(guard_root)]


def test_frickbears3_service_detects_flat_root_addon_layout(tmp_path):
    service = Frickbears3AddonsService()
    extract_dir = tmp_path / "extract"
    _write_guard_layout(extract_dir, full_name="Blox")

    inspection = service.inspect_extracted_archive(str(extract_dir))

    assert inspection.eligible is True
    assert inspection.layout == "flat_root"
    assert inspection.guard_root_dirs == [str(extract_dir)]


def test_frickbears3_service_rejects_weak_signature(tmp_path):
    service = Frickbears3AddonsService()
    extract_dir = tmp_path / "extract"
    root = extract_dir / "BrokenGuard"
    _write_text(root / "extras_info.txt", json.dumps({"FULL_NAME": "BrokenGuard"}))
    _write_bytes(root / "icon.png")

    inspection = service.inspect_extracted_archive(str(extract_dir))

    assert inspection.eligible is False


def test_frickbears3_service_converts_addon_archive_to_g3m_mod(tmp_path):
    service = Frickbears3AddonsService()
    extract_dir = tmp_path / "extract"
    guard_root = extract_dir / "Goomba"
    _write_guard_layout(guard_root, full_name="Goomba")

    mods_dir = tmp_path / "mods"
    result = service.convert_extracted_archive(
        str(extract_dir),
        str(mods_dir),
        source_file_path="goomba.zip",
        gamebanana_metadata={
            "name": "GOOMBA ~ CUSTOM GUARD",
            "mod_id": 42,
            "game": "frickbears3",
            "icon": "https://images.example.com/goomba.png",
        },
    )

    result_path = Path(result)
    config = json.loads((result_path / "mod_config.json").read_text("utf-8"))
    assert config["config_version"] == "2.0.0"
    assert config["name"] == "GOOMBA ~ CUSTOM GUARD"
    assert config["id"] == "gb_mod_42"
    assert config["game"] == "frickbears3"
    assert config["icon"] == "https://images.example.com/goomba.png"
    assert config["files"] == [
        {
            "source": "${mod_path}/addons/",
            "target": "${game_data_path}/addons/",
            "type": "extract",
        }
    ]
    assert (result_path / "addons" / "Goomba" / "extras_info.txt").exists()
    assert (result_path / "addons" / "Goomba" / "icon.png").exists()
