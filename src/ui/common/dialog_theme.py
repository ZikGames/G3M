"""Shared theme helper for dialogs."""

import re
from collections.abc import Callable
from typing import Any, cast

from PyQt6 import sip
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QAbstractButton, QDialog, QLabel, QMessageBox, QPushButton

from services.localization_service import tr
from ui.common.styling import (
    DEFAULT_COLORS,
    clamp_border_radius,
    get_border_radius,
    get_theme_colors,
    get_ui_scale_factor,
)
from utils.path_utils import resource_path


def scale_stylesheet(stylesheet: str, app_state) -> str:
    """Scale authored pixel dimensions, leaving URLs and quoted strings intact."""
    scale = get_ui_scale_factor(getattr(app_state, "local_config", None))
    parts = re.split(r'''(url\([^)]*\)|"[^"]*"|'[^']*')''', stylesheet)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(
            r"(-?\d+(?:\.\d+)?)px\b",
            lambda match: f"{round(float(match[1]) * scale)}px",
            parts[index],
        )
    return "".join(parts)


class _DialogUpdates:
    """Dialogs refresh their existing controls when appearance settings change."""

    translator: Callable[..., str] = staticmethod(tr)
    _app_state: Any

    def localize(self, setter: Callable[[str], None], key: str, *, owner: QObject | None = None, **parameters: Any) -> None:
        if getattr(self, "_text_bindings", None) is None:
            self._text_bindings = []
        target = owner if owner is not None else getattr(setter, "__self__", None)
        self._text_bindings.append((setter, key, parameters, target))
        setter(self.translator(key, **parameters))

    def theme_state(self):
        current = cast(QDialog, self)
        while current is not None:
            state = getattr(current, "app_state", None) or getattr(current, "_app_state", None)
            if state is not None:
                return state
            current = current.parentWidget()
        return None

    def set_theme_stylesheet(self, stylesheet: str) -> None:
        self._theme_stylesheet = stylesheet
        cast(QDialog, self).setStyleSheet(scale_stylesheet(stylesheet, self.theme_state()))

    def localize_text[T: QLabel | QAbstractButton](self, control: T, key: str, **parameters: Any) -> T:
        self.localize(control.setText, key, **parameters)
        return control

    def set_localized_title(self, key: str, **parameters: Any) -> None:
        self._title_translation = (key, parameters)
        cast(QDialog, self).setWindowTitle(self.translator(key, **parameters))

    def relocalize_ui(self) -> None:
        if title := getattr(self, "_title_translation", None):
            key, parameters = title
            cast(QDialog, self).setWindowTitle(self.translator(key, **parameters))
        bindings = getattr(self, "_text_bindings", ())
        self._text_bindings = [binding for binding in bindings if not isinstance(binding[3], QObject) or not sip.isdeleted(binding[3])]
        for setter, key, parameters, _owner in self._text_bindings:
            setter(self.translator(key, **parameters))

    def apply_theme(self) -> None:
        refresh = getattr(self, "refresh_theme", None) or getattr(self, "_apply_theme", None)
        if callable(refresh):
            refresh()
        else:
            apply_dialog_theme(self, self.theme_state())

    def rescale_ui(self) -> None:
        self.apply_theme()


class DynamicDialog(_DialogUpdates, QDialog):
    """A dialog with localization, theme and scale update hooks."""


class DynamicMessageBox(_DialogUpdates, QMessageBox):
    """A message box with the same update hooks as other dialogs."""

    def add_localized_button(self, key: str, role: QMessageBox.ButtonRole) -> QPushButton:
        button = cast(QPushButton, self.addButton("", role))
        return self.localize_text(button, key)


def get_dialog_theme_values(app_state):
    if not app_state:
        return {
            **DEFAULT_COLORS,
            "border_radius": get_border_radius(None),
            "button_radius": clamp_border_radius(get_border_radius(None), height=30),
            "field_radius": clamp_border_radius(get_border_radius(None), height=30),
            "checkbox_indicator_radius": clamp_border_radius(
                get_border_radius(None), width=18, height=18, border_width=2
            ),
        }
    colors = get_theme_colors(app_state.local_config)
    br = get_border_radius(app_state.local_config)
    return {
        **colors,
        "border_radius": br,
        "button_radius": clamp_border_radius(br, height=30),
        "field_radius": clamp_border_radius(br, height=30),
        "checkbox_indicator_radius": clamp_border_radius(
            br, width=18, height=18, border_width=2
        ),
    }


def build_dialog_theme_stylesheet(app_state):
    theme = get_dialog_theme_values(app_state)
    arrow_down_path = resource_path("assets/icons/arrow_down.svg").replace("\\", "/")
    return f"""
        QDialog {{
            background-color: {theme["background"]};
            color: {theme["main_text"]};
            border-radius: {theme["border_radius"]}px;
        }}
        QLineEdit {{
            background-color: {theme["elements"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["field_radius"]}px;
            color: {theme["main_text"]};
            padding: 8px;
        }}
        QComboBox {{
            background-color: {theme["elements"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["field_radius"]}px;
            color: {theme["main_text"]};
            padding: 5px 10px;
            min-height: 30px;
        }}
        QLineEdit:focus {{
            border: 2px solid {theme["hover"]};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: border;
            subcontrol-position: center right;
            background: transparent;
            border: none;
            width: 30px;
            margin-right: 4px;
        }}
        QComboBox::down-arrow {{
            image: url({arrow_down_path});
            width: 16px;
            height: 10px;
        }}
        QLineEdit:disabled, QComboBox:disabled {{
            color: #8f8f8f;
            border-color: #6f6f6f;
        }}
        QListWidget {{
            background-color: {theme["elements"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["border_radius"]}px;
            color: {theme["main_text"]};
            padding: 6px;
        }}
        QListWidget::item {{
            padding: 11px 8px;
            border-bottom: 2px solid {theme["border"]};
        }}
        QListWidget::item:selected {{
            background-color: {theme["hover"]};
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme["elements"]};
            color: {theme["main_text"]};
            selection-background-color: {theme["hover"]};
            selection-color: {theme["main_text"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["border_radius"]}px;
        }}
        QComboBox QAbstractItemView::item {{
            border: 2px solid transparent;
            border-radius: {theme["field_radius"]}px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: {theme["hover"]};
            color: {theme["main_text"]};
            border: 2px solid transparent;
            border-radius: {theme["field_radius"]}px;
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {theme["hover"]};
            color: {theme["main_text"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["field_radius"]}px;
        }}
        QPushButton {{
            background-color: {theme["elements"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["button_radius"]}px;
            color: {theme["main_text"]};
            padding: 8px 15px;
            font-weight: bold;
        }}
        QPushButton:hover:enabled {{
            background-color: {theme["hover"]};
        }}
        QPushButton:pressed:enabled {{
            background-color: {theme["hover"]};
        }}
        QPushButton:disabled {{
            background-color: {theme["background"]};
            color: #8f8f8f;
            border-color: #6f6f6f;
        }}
        QLabel {{
            color: {theme["main_text"]};
        }}
        QCheckBox {{
            color: {theme["main_text"]};
        }}
        QCheckBox:disabled {{
            color: #8f8f8f;
        }}
        QCheckBox::indicator, QTreeWidget::indicator {{
            width: 18px;
            height: 18px;
            background-color: {theme["elements"]};
            border: 2px solid {theme["border"]};
            border-radius: {theme["checkbox_indicator_radius"]}px;
        }}
        QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{
            background-color: {theme["select"]};
        }}
        QCheckBox::indicator:disabled, QTreeWidget::indicator:disabled {{
            background-color: #6f6f6f;
            border-color: #6f6f6f;
        }}
    """


def build_progress_bar_stylesheet(theme: dict[str, object]) -> str:
    return f"""
        QProgressBar {{
            background-color: {theme["background"]};
            border: 2px solid {theme["border"]};
            border-radius: 4px;
            text-align: center;
            font-size: 10px;
            color: {theme["main_text"]};
        }}
        QProgressBar::chunk {{
            background-color: {theme["secondary_text"]};
            border-radius: 3px;
        }}
    """


def get_dialog_text_color(app_state) -> str:
    """Return themed text color for dialogs."""
    from ui.common.styling import get_theme_color

    return (
        get_theme_color(app_state.local_config, "main_text") if app_state else "#e8e9eb"
    )


def apply_dialog_theme(dialog, app_state):
    """Apply consistent theme to dialog."""
    stylesheet = build_dialog_theme_stylesheet(app_state)
    if isinstance(dialog, _DialogUpdates):
        if app_state is not None and dialog.theme_state() is None:
            dialog._app_state = app_state
        dialog.set_theme_stylesheet(stylesheet)
    else:
        dialog.setStyleSheet(scale_stylesheet(stylesheet, app_state))
