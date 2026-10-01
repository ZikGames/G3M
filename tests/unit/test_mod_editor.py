from unittest.mock import Mock

from ui.dialogs.mod_editor.dialog import ModEditorDialog


def test_editor_preserves_cyop_afom_tag_when_saving_existing_mod():
    editor = ModEditorDialog.__new__(ModEditorDialog)
    editor.mod_data = {"tags": ["CYOP/AFOM"]}
    editor.mod_id = "pizza_mod"
    editor._operation_files = []
    editor._custom_placeholders = {}
    editor.name_edit = Mock(text=lambda: "Pizza mod")
    editor.version_edit = Mock(text=lambda: "1.0.0")
    editor.authors_edit = Mock(text=lambda: "Author")
    editor.game_combo = Mock(currentData=lambda: "pizzatower")
    editor.description_edit = Mock(text=lambda: "")
    editor.homepage_edit = Mock(text=lambda: "")
    editor.game_version_edit = Mock(text=lambda: "")
    editor.icon_edit = Mock(text=lambda: "")
    editor.tag_textedit = Mock(isChecked=lambda: False)
    editor.tag_customization = Mock(isChecked=lambda: False)
    editor.tag_gameplay = Mock(isChecked=lambda: False)
    editor.tag_other = Mock(isChecked=lambda: False)
    editor._relation_values = Mock(return_value=[])

    assert editor._config()["tags"] == ["CYOP/AFOM"]
