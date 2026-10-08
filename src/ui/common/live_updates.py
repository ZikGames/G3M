"""Dispatch customization changes to every live widget, including modal dialogs."""

import logging

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def _depth(widget) -> int:
    depth = 0
    while (widget := widget.parentWidget()) is not None:
        depth += 1
    return depth


def refresh_live_widgets(main_window, hook: str) -> None:
    fallbacks = {
        "relocalize_ui": ("update_labels_text",),
        "apply_theme": ("refresh_theme",),
        "rescale_ui": ("scale_ui", "apply_theme", "refresh_theme"),
    }
    live_widgets = [widget for widget in QApplication.allWidgets() if widget is not main_window and not sip.isdeleted(widget)]
    for widget in sorted(live_widgets, key=_depth):
        if widget is main_window or sip.isdeleted(widget):
            continue
        callback = getattr(widget, hook, None)
        if not callable(callback):
            callback = next((candidate for name in fallbacks[hook] if callable(candidate := getattr(widget, name, None))), None)
        if callable(callback):
            try:
                callback()
            except Exception:
                if not sip.isdeleted(widget):
                    logger.exception("Failed to %s on %s", hook, type(widget).__name__)
