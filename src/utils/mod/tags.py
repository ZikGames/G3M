"""Maps free-form tags and categories onto the tags a mod config supports."""

from __future__ import annotations

import re

from config.config import CYOP_AFOM_TAG, FB_ADDON_TAG

SUPPORTED_TAGS = {
    "textedit": "textedit",
    "customization": "customization",
    "gameplay": "gameplay",
    "other": "other",
    "cyopafom": CYOP_AFOM_TAG,
    "fbaddon": FB_ADDON_TAG,
}

_TAG_MARKERS = (
    (
        "textedit",
        (
            "text",
            "texts",
            "translation",
            "translations",
            "localization",
            "localizations",
            "dialogue",
            "dialogues",
            "subtitle",
            "subtitles",
        ),
    ),
    (
        "gameplay",
        (
            "gameplay",
            "fight",
            "fights",
            "difficulty",
            "difficulties",
            "multiplayer",
            "cyop",
            "afom",
            "tower",
            "towers",
            "level",
            "levels",
            "extension",
            "extensions",
            "full game",
            "lap",
            "laps",
            "moveset",
            "movesets",
            "move set",
            "game file",
            "editor",
            "editors",
            "rework",
            "reworks",
            "rank",
            "ranks",
            "change",
            "changes",
            "edit",
            "edits",
        ),
    ),
    (
        "customization",
        (
            "skin",
            "skins",
            "reskin",
            "reskins",
            "sprite",
            "sprites",
            "character",
            "characters",
            "animatronic",
            "animatronics",
            "guard",
            "guards",
            "office",
            "offices",
            "clothes",
            "palette",
            "palettes",
            "music",
            "sound",
            "sounds",
            "audio",
            "settings",
            "effects",
            "ui",
            "taunt",
            "taunts",
            "titlecard",
            "titlecards",
        ),
    ),
)


def normalize_tag(value: object) -> str:
    """Return the supported tag for any label; unrecognized labels become ``other``."""
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
    if supported := SUPPORTED_TAGS.get(normalized.replace(" ", "")):
        return supported
    words = set(normalized.split())
    for tag, markers in _TAG_MARKERS:
        if any(
            marker in normalized if " " in marker else marker in words
            for marker in markers
        ):
            return tag
    return "other"


def normalize_tags(values: object) -> list[str]:
    """Map every label to a supported tag, keeping first-seen order without duplicates."""
    if not isinstance(values, (list, tuple)):
        values = [values] if values else []
    return list(
        dict.fromkeys(
            normalize_tag(value)
            for value in values
            if isinstance(value, str) and value.strip()
        )
    )
