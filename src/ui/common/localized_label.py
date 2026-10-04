"""A translated status label retains its message parameters across language changes."""

from dataclasses import dataclass
from typing import Any, override

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel

from services.localization_service import tr


@dataclass(frozen=True)
class LocalizedMessage:
    key: str
    parameters: dict[str, Any]

    def render(self) -> str:
        return tr(self.key, **{key: value.render() if isinstance(value, LocalizedMessage) else value for key, value in self.parameters.items()})


class LocalizedLabel(QLabel):
    _translation: list[tuple[str, dict[str, Any]]] | None = None

    @override
    def setText(self, a0: str | None) -> None:
        self._translation = None
        super().setText(a0)

    def set_localized_text(self, key: str, **parameters: Any) -> None:
        self.set_localized_messages([(key, parameters)])

    def set_localized_messages(self, messages: list[tuple[str, dict[str, Any]]]) -> None:
        self._translation = messages
        self.relocalize_ui()

    @override
    def clear(self) -> None:
        self._translation = None
        super().clear()

    @override
    def setPixmap(self, a0: QPixmap) -> None:
        self._translation = None
        super().setPixmap(a0)

    def relocalize_ui(self) -> None:
        if self._translation is not None:
            super().setText("\n".join(LocalizedMessage(key, parameters).render() for key, parameters in self._translation))
