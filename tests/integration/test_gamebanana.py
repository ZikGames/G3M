"""Integration tests for test gamebanana."""

import os
from unittest.mock import MagicMock, Mock, patch


class TestGameBananaAPI:
    """Tests for gamebanana."""
    @patch('requests.Session')
    def test_fetch_game_mods(self, mock_session_class):
        """Checks that fetching game mods."""
        from adapters.gamebanana_adapter import GameBananaAPI
        mock_session = MagicMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'_aRecords': [{'_idRow': 12345, '_sModelName': 'Mod', '_sName': 'Test Mod', '_nDownloadCount': 1000}]}
        mock_response.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        api = GameBananaAPI()
        mods, _needing_metadata = api.get_game_mods(game_id=6755, page=1, per_page=20)

        assert mods is not None
        assert isinstance(mods, list)

    def test_map_mod_data_marks_content_rated_mod_as_nsfw(self):
        """Checks that maping mod data marks content rated mod as nsfw."""
        from adapters.gamebanana_adapter import GameBananaAPI
        api = GameBananaAPI()
        mod = api._map_mod_data({'_idRow': 657995, '_sName': 'Roaring Knight: Berserk', '_nDownloadCount': 34, '_aTags': ['Boss: Roaring Knight'], '_bHasContentRatings': True}, 'deltarune')
        assert mod is not None
        assert mod.is_nsfw is True

    @patch('requests.Session')
    def test_get_mod_profile_page(self, mock_session_class):
        """Checks that getting mod profile page."""
        from adapters.gamebanana_adapter import GameBananaAPI
        mock_session = MagicMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'_idRow': 12345, '_sName': 'Test Mod', '_sDescription': 'A test mod'}
        mock_response.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        api = GameBananaAPI()
        details = api.get_mod_profile_page(mod_id=12345)
        assert details is None or isinstance(details, dict)

    @patch('requests.Session')
    def test_get_supported_files(self, mock_session_class):
        """Checks that getting supported files."""
        from adapters.gamebanana_adapter import GameBananaAPI
        mock_session = MagicMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'_aFiles': [{'_idRow': 1, '_sFile': 'mod.zip', '_nDownloadCount': 500}]}
        mock_response.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        api = GameBananaAPI()
        result = api.get_supported_files_for_mod(mod_id=12345)
        assert isinstance(result, dict)
        assert 'supported_files' in result
        assert 'has_supported_files' in result
        assert 'compatibility_checked' in result
        assert 'preferred_format' in result
        assert 'tool_ids' in result
        assert 'has_g3m_file' in result
        assert 'has_deltamod_file' in result
        assert isinstance(result['supported_files'], list)
        assert isinstance(result['has_supported_files'], bool)
        assert isinstance(result['compatibility_checked'], bool)
        assert isinstance(result['tool_ids'], list)
        assert isinstance(result['has_g3m_file'], bool)
        assert isinstance(result['has_deltamod_file'], bool)

    @patch('requests.Session')
    def test_get_supported_files_with_itemtype(self, mock_session_class):
        """Checks that getting supported files with itemtype."""
        from adapters.gamebanana_adapter import GameBananaAPI
        mock_session = MagicMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'_aFiles': [{'_idRow': 1, '_sFile': 'mod.zip', '_nDownloadCount': 500}]}
        mock_response.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        api = GameBananaAPI()
        result = api.get_supported_files_for_mod(mod_id=12345, itemtype='Wip')
        assert isinstance(result, dict)
        assert 'supported_files' in result
        assert 'has_supported_files' in result
        assert 'compatibility_checked' in result

    def test_resolve_dependency_uses_only_a_supported_file(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(
            return_value={
                "supported_files": [
                    {
                        "id": 88,
                        "name": "dependency.g3m",
                        "download_url": "https://example.test/dependency.g3m",
                        "size_bytes": 2048,
                        "md5": "d41d8cd98f00b204e9800998ecf8427e",
                        "compatibility": "g3m",
                    }
                ]
            }
        )
        api.get_mod_profile_page = Mock(
            return_value={
                "_sName": "Dependency",
                "_sVersion": "2.0.0",
                "_aSubmitter": {"_sName": "Author"},
            }
        )

        result = api.resolve_dependency_download("gb_mod_123", "deltarune")

        assert result is not None
        assert result["canonical_key"] == "gb_mod_123_88"
        assert result["metadata"]["gb_mod_id"] == 123
        assert result["metadata"]["file_name"] == "dependency.g3m"
        assert result["metadata"]["size_bytes"] == 2048
        assert result["metadata"]["md5"] == "d41d8cd98f00b204e9800998ecf8427e"

    def test_resolve_dependency_rejects_noncanonical_ids(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()

        assert api.resolve_dependency_download("gb_mod_123_extra", "deltarune") is None

    def test_resolve_supported_dependency_constructs_url_from_file_id(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(return_value={
            "supported_files": [{"id": 88, "name": "dependency.zip", "compatibility": "g3m"}],
        })
        api.get_mod_profile_page = Mock(return_value={"_sName": "Dependency"})

        result = api.resolve_dependency_download("gb_mod_123", "deltarune")

        assert result is not None
        assert result["source_url"] == "https://gamebanana.com/dl/88"
        assert result["canonical_key"] == "gb_mod_123_88"

    def test_resolve_dependency_prefers_the_newest_supported_file(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(
            return_value={
                "supported_files": [
                    {
                        "id": 18,
                        "name": "mod_v18.zip",
                        "timestamp": 18,
                        "download_url": "https://gamebanana.com/dl/18",
                        "compatibility": "g3m",
                    },
                    {
                        "id": 19,
                        "name": "mod_v19.zip",
                        "timestamp": 19,
                        "download_url": "https://gamebanana.com/dl/19",
                        "compatibility": "g3m",
                    },
                ]
            }
        )
        api.get_mod_profile_page = Mock(return_value={"_sVersion": "V19"})

        result = api.resolve_dependency_download("gb_mod_123", "deltarune")

        assert result is not None
        assert result["canonical_key"] == "gb_mod_123_19"
        assert result["metadata"]["version"] == "V19"

    def test_resolve_mod_updates_offers_only_the_latest_version_files(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(return_value={"supported_files": []})
        api.get_mod_profile_page = Mock(
            return_value={
                "_aFiles": [
                    {"_idRow": 1, "_sFile": "old.zip", "_sVersion": "1.0.0"},
                    {"_idRow": 2, "_sFile": "windows.zip", "_sVersion": "1.0.1"},
                    {"_idRow": 3, "_sFile": "linux.zip", "_sVersion": "1.0.1"},
                ]
            }
        )

        results = api.resolve_mod_update_downloads("gb_mod_123", "deltarune")

        assert [result["metadata"]["gb_file_id"] for result in results] == [2, 3]
        assert {result["metadata"]["version"] for result in results} == {"1.0.1"}

    def test_resolve_mod_updates_normalizes_equivalent_versions(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(return_value={"supported_files": []})
        api.get_mod_profile_page = Mock(
            return_value={
                "_aFiles": [
                    {
                        "_idRow": 1,
                        "_sFile": "old.zip",
                        "_sVersion": "1.0.0",
                        "_tsDateAdded": 10,
                    },
                    {
                        "_idRow": 2,
                        "_sFile": "replacement.zip",
                        "_sVersion": "1.0",
                        "_tsDateAdded": 20,
                    },
                ]
            }
        )

        results = api.resolve_mod_update_downloads("gb_mod_123", "deltarune")

        assert [result["metadata"]["gb_file_id"] for result in results] == [2, 1]

    def test_resolve_mod_updates_includes_newer_unversioned_file(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(return_value={"supported_files": []})
        api.get_mod_profile_page = Mock(
            return_value={
                "_sVersion": "1.0.0",
                "_aFiles": [
                    {
                        "_idRow": 1,
                        "_sFile": "old.zip",
                        "_sVersion": "1.0.0",
                        "_tsDateAdded": 10,
                    },
                    {
                        "_idRow": 2,
                        "_sFile": "replacement.zip",
                        "_tsDateAdded": 20,
                    },
                ],
            }
        )

        results = api.resolve_mod_update_downloads("gb_mod_123", "deltarune")

        assert [result["metadata"]["gb_file_id"] for result in results] == [2, 1]

    def test_resolve_dependency_falls_back_to_newest_available_file(self):
        from adapters.gamebanana_adapter import GameBananaAPI

        api = GameBananaAPI()
        api.get_supported_files_for_mod = Mock(return_value={"supported_files": []})
        api.get_mod_profile_page = Mock(
            return_value={
                "_aFiles": [
                    {
                        "_idRow": 2,
                        "_sFile": "old.zip",
                        "_tsDateAdded": 10,
                    },
                    {
                        "_idRow": 3,
                        "_sFile": "new.zip",
                        "_tsDateAdded": 20,
                    },
                ]
            }
        )

        result = api.resolve_dependency_download("gb_mod_123", "deltarune")

        assert result is not None
        assert result["canonical_key"] == "gb_mod_123_3"
        assert result["source_url"] == "https://gamebanana.com/dl/3"


class TestGameBananaConverter:
    """Tests for gamebanana."""
    def test_convert_gamebanana_mod(self, temp_mods_dir):
        """Checks that converting gamebanana mod."""
        import tempfile
        import zipfile

        from adapters.gamebanana_converter import GameBananaConverter
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_archive:
            archive_path = tmp_archive.name
            with zipfile.ZipFile(archive_path, 'w') as zf:
                zf.writestr('meta.json', '{"metadata": {"name": "Test Mod"}}')
                zf.writestr('file1.txt', 'test')
        try:
            converter = GameBananaConverter(archive_path=archive_path, mods_dir=temp_mods_dir, gamebanana_metadata={'mod_id': 12345})
            assert converter is not None
        finally:
            os.unlink(archive_path)

    def test_deltamod_data_replacement_is_an_overwrite(self):
        from adapters.deltamod_adapter import DeltamodConverter

        converter = DeltamodConverter("unused", "unused")
        operations = converter._generate_operations(
            [
                {"type": "xdelta", "patch": "replacement.win", "to": "chapter1_windows/data.win"},
                {"type": "xdelta", "patch": "delta.xdelta", "to": "chapter1_windows/data.win"},
            ]
        )

        assert [operation["type"] for operation in operations] == ["overwrite", "patch"]

    def test_failed_conversion_removes_differently_named_replacement(self, temp_mods_dir):
        from adapters.gamebanana_converter import GameBananaConverter

        old_mod = os.path.join(temp_mods_dir, "Old Mod")
        replacement = os.path.join(temp_mods_dir, "New Mod")
        os.makedirs(old_mod)
        os.makedirs(replacement)
        converter = GameBananaConverter("unused", temp_mods_dir)
        converter._previous_mod_dir = old_mod
        converter._converted_mod_dir = replacement

        converter._remove_failed_conversion()

        assert os.path.isdir(old_mod)
        assert not os.path.exists(replacement)

    def test_convert_gamebanana_revision_four_deltamod(self, temp_mods_dir, tmp_path):
        import json
        import zipfile

        from adapters.gamebanana_converter import GameBananaConverter

        archive_path = tmp_path / "revision4.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "meta.toml",
                """
[metadata]
name = "Current Deltamod"
version = "1.0.0"
author = ["Author"]
game = "toby.deltarune"
packageID = "example.current.author"
""",
            )
            archive.writestr(
                "modding.xml",
                '<patch type="g3mpatch" patch="./mod.g3mpatch" '
                'to="./chapter1_windows/data.win" />',
            )
            archive.writestr("mod.g3mpatch", b"patch")

        result = GameBananaConverter(
            archive_path=str(archive_path),
            mods_dir=temp_mods_dir,
            gamebanana_metadata={"mod_id": 12345},
        ).convert()

        assert result is not None
        config_path = os.path.join(result, "mod_config.json")
        with open(config_path, encoding="utf-8") as config_file:
            config = json.load(config_file)
        assert config["config_version"] == "2.0.0"
        assert config["id"] == "gb_mod_12345"
        assert config["files"][0] == {
            "source": "${mod_path}/chapter_1/mod.g3mpatch",
            "type": "patch",
            "target": "${game_path}/chapter1_windows/data.win",
        }

    def test_update_preserves_existing_mod_versions(self, temp_mods_dir, tmp_path):
        import json
        import zipfile

        from adapters.gamebanana_converter import GameBananaConverter

        previous = os.path.join(temp_mods_dir, "Current Deltamod")
        os.makedirs(os.path.join(previous, "mod_versions"))
        with open(os.path.join(previous, "mod_config.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config_version": "2.0.0",
                    "id": "gb_mod_12345",
                    "name": "Current Deltamod",
                    "version": "1.0.0",
                    "authors": ["Author"],
                    "game": "deltarune",
                    "files": [],
                },
                handle,
            )
        with zipfile.ZipFile(
            os.path.join(previous, "mod_versions", "1.0.0.zip"), "w"
        ) as archive:
            archive.writestr("old.txt", "old")
        archive_path = tmp_path / "update.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "meta.toml",
                """
[metadata]
name = "Current Deltamod"
version = "2.0.0"
author = ["Author"]
game = "toby.deltarune"
packageID = "example.current.author"
""",
            )
            archive.writestr(
                "modding.xml",
                '<patch type="g3mpatch" patch="./mod.g3mpatch" '
                'to="./chapter1_windows/data.win" />',
            )
            archive.writestr("mod.g3mpatch", b"patch")

        result = GameBananaConverter(
            archive_path=str(archive_path),
            mods_dir=temp_mods_dir,
            gamebanana_metadata={"mod_id": 12345, "version": "2.0.0"},
        ).convert()

        assert result is not None
        assert result == previous
        assert os.path.isfile(os.path.join(result, "mod_versions", "1.0.0.zip"))
        with open(os.path.join(result, "mod_config.json"), encoding="utf-8") as handle:
            assert json.load(handle)["version"] == "2.0.0"

    def test_failed_update_keeps_staged_mod_for_manual_recovery(
        self, temp_mods_dir, monkeypatch
    ):
        import json
        import shutil

        from adapters import deltamod_adapter
        from adapters.gamebanana_converter import GameBananaConverter

        previous = os.path.join(temp_mods_dir, "Current Deltamod")
        os.makedirs(previous)
        with open(os.path.join(previous, "mod_config.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config_version": "2.0.0",
                    "id": "gb_mod_12345",
                    "name": "Current Deltamod",
                    "version": "1.0.0",
                    "authors": ["Author"],
                    "game": "deltarune",
                    "files": [],
                },
                handle,
            )

        class FailedConverter:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            @staticmethod
            def convert() -> None:
                return None

        converter = GameBananaConverter(
            archive_path="unused.zip",
            mods_dir=temp_mods_dir,
            gamebanana_metadata={"mod_id": 12345},
        )
        converter._check_compatibility = Mock(return_value=True)
        converter._extract_archive = Mock()
        monkeypatch.setattr(
            "adapters.gamebanana_converter.normalize_mod_package", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(deltamod_adapter, "DeltamodConverter", FailedConverter)
        original_move = shutil.move

        def fail_restore(source, destination, *args, **kwargs):
            if source == converter._previous_mod_backup:
                raise OSError("restore failed")
            return original_move(source, destination, *args, **kwargs)

        monkeypatch.setattr("adapters.gamebanana_converter.shutil.move", fail_restore)

        assert converter.convert() is None
        assert converter._previous_mod_backup is not None
        backup = converter._previous_mod_backup
        assert os.path.isfile(os.path.join(backup, "mod_config.json"))
        shutil.rmtree(os.path.dirname(backup))

    def test_update_restores_previous_mod_when_metadata_write_fails(
        self, temp_mods_dir, monkeypatch
    ):
        import json

        from adapters import deltamod_adapter
        from adapters.gamebanana_converter import GameBananaConverter

        previous = os.path.join(temp_mods_dir, "Current Deltamod")
        os.makedirs(previous)
        with open(os.path.join(previous, "mod_config.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config_version": "2.0.0",
                    "id": "gb_mod_12345",
                    "name": "Current Deltamod",
                    "version": "1.0.0",
                    "authors": ["Author"],
                    "game": "deltarune",
                    "files": [],
                },
                handle,
            )

        class ConverterThatWritesNewConfig:
            def __init__(self, _source_dir, mods_dir, _metadata) -> None:
                self.mods_dir = mods_dir

            def convert(self) -> str:
                result = os.path.join(self.mods_dir, "Current Deltamod")
                os.makedirs(result)
                with open(os.path.join(result, "mod_config.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "config_version": "2.0.0",
                            "id": "gb_mod_12345",
                            "name": "Current Deltamod",
                            "version": "2.0.0",
                            "authors": ["Author"],
                            "game": "deltarune",
                            "files": [],
                        },
                        handle,
                    )
                return result

        converter = GameBananaConverter(
            archive_path="unused.zip",
            mods_dir=temp_mods_dir,
            gamebanana_metadata={"mod_id": 12345, "version": "2.0.0"},
        )
        converter._check_compatibility = Mock(return_value=True)
        converter._extract_archive = Mock()
        monkeypatch.setattr(
            "adapters.gamebanana_converter.normalize_mod_package",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(deltamod_adapter, "DeltamodConverter", ConverterThatWritesNewConfig)
        monkeypatch.setattr(
            "adapters.gamebanana_converter.write_mod_config",
            Mock(side_effect=OSError("disk full")),
        )

        assert converter.convert() is None
        with open(os.path.join(previous, "mod_config.json"), encoding="utf-8") as handle:
            assert json.load(handle)["version"] == "1.0.0"
        assert [entry.name for entry in os.scandir(temp_mods_dir)] == [
            "Current Deltamod"
        ]
