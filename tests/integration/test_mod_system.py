"""Integration tests for test mod system."""

import json
import os
from pathlib import Path

from utils.mod.config import parse_mod_config


class TestModStructure:
    """Tests for mod system."""
    def test_mod_config_parsing(self, full_mod_structure_dir):
        """Checks that mod config parsing."""
        config_path = Path(full_mod_structure_dir) / 'mod_config.json'
        assert config_path.is_file(), 'mod_config.json not found.'
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        assert 'id' in config
        assert 'name' in config
        assert 'version' in config
        assert 'files' in config
        assert config['config_version'] == '2.0.0'
        assert isinstance(config['files'], list)
        assert parse_mod_config(config) == config

    def test_mod_structure_validation(self, full_mod_structure_dir):
        """Checks that mod structure validation."""
        mod_path = Path(full_mod_structure_dir)
        config_path = mod_path / 'mod_config.json'
        assert config_path.is_file(), 'mod_config.json not found.'
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        for operation in config.get('files', []):
            source = operation['source']
            assert source.startswith('${mod_path}/')
            source_path = mod_path / source.removeprefix('${mod_path}/')
            assert source_path.is_file(), source_path
            assert operation['target'].startswith('${game_path}/')

    def test_mod_file_discovery(self, full_mod_structure_dir):
        """Checks that mod file discovery."""
        mod_path = Path(full_mod_structure_dir)
        assert mod_path.is_dir(), f'Test mod structure not found at {full_mod_structure_dir}.'
        chapter_dirs = [d for d in mod_path.iterdir() if d.is_dir() and d.name.startswith('chapter_')]
        assert len(chapter_dirs) > 0, 'No chapter directories found'
        for chapter_dir in chapter_dirs:
            files = list(chapter_dir.glob('*'))
            assert any(f.suffix in ['.win', '.xdelta', '.vcdiff'] for f in files), chapter_dir
            assert chapter_dir.is_dir()


class TestModManagerIntegration:
    """Tests for mod system."""
    def test_mod_scanning(self, app_state, feedback_service, mods_dir):
        """Checks that mod scanning."""
        from services.mod.service import ModManager
        app_state.mods_dir = mods_dir
        mod_service = ModManager(app_state, feedback_service)
        cache = mod_service._get_mods_cache(use_async=False)
        assert isinstance(cache, dict)

    def test_mod_scanning_all_types(self, app_state, feedback_service, all_test_mods_dirs):
        """Checks that mod scanning all types."""
        import shutil
        import tempfile

        from services.mod.service import ModManager
        temp_mods_dir = tempfile.mkdtemp()
        try:
            for _mod_name, mod_path in all_test_mods_dirs.items():
                if os.path.exists(mod_path):
                    config_path = os.path.join(mod_path, 'mod_config.json')
                    if os.path.exists(config_path):
                        target_path = os.path.join(temp_mods_dir, os.path.basename(mod_path))
                        shutil.copytree(mod_path, target_path, dirs_exist_ok=True)
            app_state.mods_dir = temp_mods_dir
            mod_service = ModManager(app_state, feedback_service)
            cache = mod_service._get_mods_cache(use_async=False)
            assert isinstance(cache, dict)
        finally:
            shutil.rmtree(temp_mods_dir, ignore_errors=True)

    def test_mod_loading(self, app_state, feedback_service, full_mod_structure_dir):
        """Checks that mod loading."""
        import shutil
        import tempfile

        from services.mod.service import ModManager
        temp_mods_dir = tempfile.mkdtemp()
        mod_name = Path(full_mod_structure_dir).name
        target_mod_dir = os.path.join(temp_mods_dir, mod_name)
        try:
            if os.path.exists(full_mod_structure_dir):
                shutil.copytree(full_mod_structure_dir, target_mod_dir)
                app_state.mods_dir = temp_mods_dir
                mod_service = ModManager(app_state, feedback_service)
                cache = mod_service._get_mods_cache(use_async=False)
                config_path = os.path.join(target_mod_dir, 'mod_config.json')
                if os.path.exists(config_path):
                    with open(config_path, encoding='utf-8') as f:
                        config = json.load(f)
                    key = config.get('id')
                    if key:
                        assert key in cache or any(info.id == key for info in cache.values())
        finally:
            shutil.rmtree(temp_mods_dir, ignore_errors=True)


class TestModInstallation:
    """Tests for mod system."""
    def test_mod_installation_structure(self, app_state, feedback_service, full_mod_structure_dir):
        """Checks that mod installation structure."""
        from services.mod.service import ModManager
        _ = ModManager(app_state, feedback_service)
        mod_path = Path(full_mod_structure_dir)
        config_path = mod_path / 'mod_config.json'
        if config_path.exists():
            with open(config_path, encoding='utf-8') as f:
                config = json.load(f)
            assert 'files' in config
            assert config['config_version'] == '2.0.0'
            assert isinstance(config['files'], list)


class TestModProcessing:
    """Tests for mod system."""
    def test_mod_file_resolution(self, full_mod_structure_dir):
        """Checks that mod file resolution."""
        mod_path = Path(full_mod_structure_dir)
        config_path = mod_path / 'mod_config.json'
        assert config_path.is_file(), 'mod_config.json not found.'
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        for operation in config.get('files', []):
            source = operation['source']
            source_path = mod_path / source.removeprefix('${mod_path}/')
            assert source_path.is_file(), source_path

    def test_mod_chapter_mapping(self, full_mod_structure_dir):
        """Checks that mod chapter mapping."""
        mod_path = Path(full_mod_structure_dir)
        config_path = mod_path / 'mod_config.json'
        assert config_path.is_file(), 'mod_config.json not found.'
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        targets = {operation['target'] for operation in config.get('files', [])}
        assert targets == {
            '${game_path}/data.win',
            '${game_path}/chapter1_windows/data.win',
            '${game_path}/chapter2_windows/data.win',
        }


class TestModMetadata:
    """Tests for mod system."""
    def test_metadata_read_write(self, app_state, feedback_service):
        """Checks that metadata read write."""
        import time

        from services.mod.service import ModManager
        mod_service = ModManager(app_state, feedback_service)
        metadata = mod_service._read_metadata()
        assert isinstance(metadata, dict), "_read_metadata should return a dict, even if file doesn't exist"
        if not os.path.exists(app_state.mods_metadata_path):
            assert metadata == {}, 'Empty metadata should return empty dict'
        test_metadata = {'test_mod_001': {'added_date': time.strftime('%Y-%m-%d %H:%M:%S'), 'is_gamebanana': True}, 'test_mod_002': {'added_date': '2024-01-01 00:00:00', 'is_gamebanana': False}}
        mod_service._write_metadata(test_metadata)
        assert os.path.exists(app_state.mods_metadata_path), 'Metadata file should be created after write'
        written_metadata = mod_service._read_metadata()
        assert isinstance(written_metadata, dict), 'Should read metadata as dict'
        assert 'test_mod_001' in written_metadata, 'Written mod should be in read metadata'
        assert 'test_mod_002' in written_metadata, 'All written mods should be in read metadata'
        assert written_metadata['test_mod_001']['is_gamebanana'], 'Metadata values should be preserved'
        assert written_metadata['test_mod_002']['is_gamebanana'] is False, 'All metadata values should be preserved'
        assert written_metadata['test_mod_002']['added_date'] == '2024-01-01 00:00:00', 'Dates should be preserved'
        mod_service._write_metadata({})

    def test_metadata_file_creation(self, app_state, feedback_service):
        """Checks that metadata file creation."""
        import json
        import os

        from services.mod.service import ModManager
        mod_service = ModManager(app_state, feedback_service)
        if os.path.exists(app_state.mods_metadata_path):
            os.remove(app_state.mods_metadata_path)
        assert not os.path.exists(app_state.mods_metadata_path), 'Metadata file should not exist initially'
        test_metadata = {'test_mod': {'added_date': '2024-01-01 00:00:00'}}
        mod_service._write_metadata(test_metadata)
        assert os.path.exists(app_state.mods_metadata_path), 'Metadata file should be created after write'
        with open(app_state.mods_metadata_path, encoding='utf-8') as f:
            file_content = json.load(f)
        assert isinstance(file_content, dict), 'Metadata file should contain valid JSON dict'
        assert 'test_mod' in file_content, 'Written mod should be in file'
        assert file_content['test_mod']['added_date'] == '2024-01-01 00:00:00', 'Data in file should match written data'
        if os.path.exists(app_state.mods_metadata_path):
            os.remove(app_state.mods_metadata_path)

    def test_metadata_with_installed_mods(self, app_state, feedback_service, full_mod_structure_dir):
        """Checks that metadata with installed mods."""
        import json
        import shutil
        import tempfile

        from services.mod.service import ModManager
        temp_mods_dir = tempfile.mkdtemp()
        mod_name = Path(full_mod_structure_dir).name
        target_mod_dir = os.path.join(temp_mods_dir, mod_name)
        try:
            if os.path.exists(full_mod_structure_dir):
                shutil.copytree(full_mod_structure_dir, target_mod_dir)
                app_state.mods_dir = temp_mods_dir
                mod_service = ModManager(app_state, feedback_service)
                _ = mod_service._get_mods_cache(use_async=False)
                installed_mods = mod_service.get_installed_mods_list()
                assert isinstance(installed_mods, list)
                config_path = os.path.join(target_mod_dir, 'mod_config.json')
                if os.path.exists(config_path):
                    with open(config_path, encoding='utf-8') as f:
                        config = json.load(f)
                    key = config.get('id')
                    if key:
                        metadata = mod_service._read_metadata()
                        assert isinstance(metadata, dict)
        finally:
            shutil.rmtree(temp_mods_dir, ignore_errors=True)

    def test_metadata_preserved_after_load_local_mods_cleanup(self, app_state, feedback_service):
        """Checks that metadata preserved after load local mods cleanup."""
        from services.mod.service import ModManager
        mod_service = ModManager(app_state, feedback_service)
        original_date = '2024-06-15 12:30:00'
        seed_metadata = {
            'test_mod_abc': {'added_date': original_date, 'is_gamebanana': True},
            'mod_files_to_cleanup': [],
            'mod_dirs_to_cleanup': [],
        }
        mod_service._write_metadata(seed_metadata)
        mod_service.load_local_mods()
        after = mod_service._read_metadata()
        assert 'test_mod_abc' not in after or after['test_mod_abc']['added_date'] == original_date, \
            'Existing metadata added_date must not be overwritten by load_local_mods'
        assert 'mod_files_to_cleanup' not in after, 'Cleanup keys should be removed'
        assert 'mod_dirs_to_cleanup' not in after, 'Cleanup keys should be removed'
        mod_service._write_metadata({})

    def test_legacy_cleanup_metadata_never_deletes_files(self, app_state, feedback_service, tmp_path, monkeypatch):
        from services.mod.service import ModManager

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        outside_dir = tmp_path / "unrelated"
        outside_dir.mkdir()
        sentinel = outside_dir / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        app_state.mods_dir = str(mods_dir)
        app_state.mods_metadata_path = str(mods_dir / "mods_data.json")
        mod_service = ModManager(app_state, feedback_service)
        monkeypatch.setattr(mod_service, "_get_mods_cache", lambda **_kwargs: {})
        monkeypatch.setattr(mod_service, "_migrate_owned_mods", lambda: None)
        mod_service._write_metadata({
            "mod_files_to_cleanup": [str(sentinel)],
            "mod_dirs_to_cleanup": [str(outside_dir)],
        })

        assert mod_service.load_local_mods()

        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert mod_service._read_metadata() == {}
