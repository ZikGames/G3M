from unittest.mock import Mock

import pytest

from ui.dialogs.mod_editor.dialog import ModEditorDialog


def _editor(game, checked=()):
    editor = ModEditorDialog.__new__(ModEditorDialog)
    editor.mod_data = {}
    editor.mod_id = "special_mod"
    editor._operation_files = []
    editor._custom_placeholders = {}
    editor.name_edit = Mock(text=lambda: "Special mod")
    editor.version_edit = Mock(text=lambda: "1.0.0")
    editor.authors_edit = Mock(text=lambda: "Author")
    editor.game_combo = Mock(currentData=lambda: game)
    editor.description_edit = Mock(text=lambda: "")
    editor.homepage_edit = Mock(text=lambda: "")
    editor.game_version_edit = Mock(text=lambda: "")
    editor.icon_edit = Mock(text=lambda: "")
    for name in ("textedit", "customization", "gameplay", "other"):
        setattr(editor, f"tag_{name}", Mock(isChecked=lambda: False))
    vars(editor)["_game_tag_boxes"] = {
        tag: (Mock(isChecked=lambda: True, setChecked=Mock(), setVisible=Mock()), game_id)
        for tag, game_id in (("CYOP/AFOM", "pizzatower"), ("FB-Addon", "frickbears3"))
    }
    editor._relation_values = Mock(return_value=[])
    return editor


@pytest.mark.parametrize(
    ("game", "tag"), [("pizzatower", "CYOP/AFOM"), ("frickbears3", "FB-Addon")]
)
def test_editor_saves_a_game_tag_only_for_its_game(game, tag):
    assert _editor(game)._config()["tags"] == [tag]
    assert "tags" not in _editor("deltarune")._config()


def test_editor_offers_a_game_tag_only_while_its_game_is_selected():
    editor = _editor("frickbears3")

    editor._refresh_game_tags()

    boxes = vars(editor)["_game_tag_boxes"]
    shown = {tag: box.setVisible.call_args.args[0] for tag, (box, _game) in boxes.items()}
    assert shown == {"CYOP/AFOM": False, "FB-Addon": True}
    boxes["CYOP/AFOM"][0].setChecked.assert_called_once_with(False)
