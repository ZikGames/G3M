"""Unit tests for test pizza tower afom service."""

import json
from pathlib import Path

from services.pizza_tower_afom_service import PizzaTowerAFOMService


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_afom_service_detects_single_root_archive_layout(tmp_path):
    service = PizzaTowerAFOMService()
    extract_dir = tmp_path / "extract"
    root = extract_dir / "Crumbling_Tower_Supreme"
    _write_text(
        root / "Crumbling_Tower_Supreme.tower.ini",
        '[properties]\nmainlevel="tower"\nname="The Crumbling Tower of Pizza Supreme"\n',
    )
    _write_text(root / "levels" / "Supreme" / "level.ini", "[properties]\n")

    inspection = service.inspect_extracted_archive(str(extract_dir))

    assert inspection.eligible is True
    assert inspection.root_dirs == [str(root)]


def test_afom_service_rejects_root_with_loose_files(tmp_path):
    service = PizzaTowerAFOMService()
    extract_dir = tmp_path / "extract"
    root = extract_dir / "TowerOne"
    _write_text(
        root / "tower.ini",
        '[properties]\nmainlevel="tower"\nname="Tower One"\n',
    )
    _write_text(extract_dir / "readme.txt", "loose")

    inspection = service.inspect_extracted_archive(str(extract_dir))

    assert inspection.eligible is False


def test_afom_service_converts_multi_root_archive_to_towers_mod(tmp_path):
    service = PizzaTowerAFOMService()
    extract_dir = tmp_path / "extract"
    first_root = extract_dir / "TowerOne"
    second_root = extract_dir / "TowerTwo"
    for root, name in ((first_root, "Tower One"), (second_root, "Tower Two")):
        _write_text(
            root / f"{root.name}.tower.ini",
            f'[properties]\nmainlevel="tower"\nname="{name}"\n',
        )
        _write_text(root / "levels" / root.name / "level.ini", "[properties]\n")

    mods_dir = tmp_path / "mods"
    result = service.convert_extracted_archive(
        str(extract_dir),
        str(mods_dir),
        source_file_path="multi_afom.zip",
        gamebanana_metadata={"name": "Converted AFOM", "mod_id": 42, "game": "pizzatower"},
    )

    result_path = Path(result)
    config = json.loads((result_path / "mod_config.json").read_text("utf-8"))
    assert config["config_version"] == "2.0.0"
    assert config["name"] == "Converted AFOM"
    assert config["id"] == "gb_mod_42"
    assert config["tags"] == ["CYOP/AFOM"]
    assert config["files"] == [
        {
            "source": "${mod_path}/towers/",
            "target": "${game_data_path}/towers/",
            "type": "extract",
        }
    ]
    assert (result_path / "towers" / "TowerOne" / "TowerOne.tower.ini").exists()
    assert (result_path / "towers" / "TowerTwo" / "TowerTwo.tower.ini").exists()


def test_afom_config_retains_only_supported_metadata_tags():
    config = PizzaTowerAFOMService._build_config_data(
        "AFOM",
        {"tags": ["textedit", "unsupported", "CYOP/AFOM", "textedit"]},
    )

    assert config["tags"] == ["CYOP/AFOM", "textedit"]
