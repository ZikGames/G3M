from types import SimpleNamespace

from PyQt6.QtCore import Qt

from ui.dialogs.mod.updates_dialog import ModUpdatesDialog


def test_mod_updates_dialog_groups_and_emits_selected_updates(qapp):
    dialog = ModUpdatesDialog(
        SimpleNamespace(local_config={}), ["Default"], "Default"
    )
    requested = []
    dialog.updates_requested.connect(lambda updates, replace: requested.append((updates, replace)))
    candidate = {
        "id": "gb_mod_123",
        "name": "Example",
        "game": "deltarune",
        "version": "1.0.0",
        "resolved": {"metadata": {"version": "2.0.0"}},
    }
    second_candidate = {**candidate, "id": "gb_mod_456", "name": "Another"}

    dialog.set_candidates([candidate, second_candidate])
    dialog._request_updates()

    assert dialog._tree.topLevelItemCount() == 1
    assert dialog.minimumWidth() >= 840
    game_item = dialog._tree.topLevelItem(0)
    assert dialog._tree.rootIsDecorated()
    assert dialog._tree.itemsExpandable()
    assert game_item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert game_item.childCount() == 2
    game_item.setCheckState(0, Qt.CheckState.Unchecked)
    assert all(
        game_item.child(index).checkState(0) == Qt.CheckState.Unchecked
        for index in range(game_item.childCount())
    )
    game_item.child(0).setCheckState(0, Qt.CheckState.Checked)
    dialog.relocalize_ui()
    game_item = dialog._tree.topLevelItem(0)
    assert game_item.child(0).checkState(0) == Qt.CheckState.Checked
    assert game_item.child(1).checkState(0) == Qt.CheckState.Unchecked
    assert "QTreeWidget::indicator" in dialog.styleSheet()
    assert requested == [([second_candidate, candidate], False)]


def test_mod_updates_dialog_keeps_manual_install_outcome_after_refresh(qapp):
    dialog = ModUpdatesDialog(
        SimpleNamespace(local_config={}), ["Default"], "Default"
    )

    dialog.set_outcome("Manual installation is required in Downloads.")
    dialog.set_checking(preserve_outcome=True)
    dialog.set_candidates([])

    assert not dialog._outcome.isHidden()
    assert dialog._outcome.text() == "Manual installation is required in Downloads."
