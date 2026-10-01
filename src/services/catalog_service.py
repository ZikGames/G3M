"""Loads the separate plugin and theme catalogs."""

from __future__ import annotations

import logging
import time

from config.config import PLUGIN_CATALOG_URL, THEME_CATALOG_URL
from models.catalog_models import CatalogThemeEntry
from models.plugin_models import CatalogPluginEntry

logger = logging.getLogger(__name__)


class CatalogService:
    _CACHE_TTL_SECONDS = 300

    def __init__(self, app_state, settings_service, plugins_dir: str) -> None:
        self.app_state = app_state
        self.settings_service = settings_service
        self.plugins_dir = plugins_dir
        self._catalog: dict | None = None
        self._catalog_loaded_at = 0.0
        self._plugin_entries: list[CatalogPluginEntry] | None = None
        self._theme_entries: list[CatalogThemeEntry] | None = None

    def load_catalog(self, force_refresh: bool = False) -> dict:
        if self._catalog is not None and not force_refresh and time.time() - self._catalog_loaded_at < self._CACHE_TTL_SECONDS:
            return self._catalog
        if force_refresh:
            data = self._try_fetch_catalog()
            if data:
                self._catalog = data
                self._catalog_loaded_at = time.time()
                self._invalidate_cache()
                return data
        self._catalog = self._catalog or {}
        self._catalog_loaded_at = time.time() if self._catalog else 0.0
        self._invalidate_cache()
        return self._catalog

    def refresh_catalog(self) -> dict:
        return self.load_catalog(force_refresh=True)

    def is_loaded(self) -> bool:
        return bool(self._catalog and (self._catalog.get("plugins") or self._catalog.get("themes")))

    def list_plugins(self, *, load_if_needed: bool = True) -> list[CatalogPluginEntry]:
        if self._plugin_entries is None or (load_if_needed and self._catalog is None):
            catalog = self.load_catalog() if load_if_needed else (self._catalog or {})
            self._plugin_entries = [
                CatalogPluginEntry(
                    id=str(item.get("id", "")).strip(), name=str(item.get("name", "")).strip(),
                    description=str(item.get("description", "")).strip(), author=str(item.get("author", "")).strip(),
                    version=str(item.get("version", "")).strip(), api_version=str(item.get("api_version", "")).strip(),
                    icon=str(item.get("icon", "")).strip(), homepage=str(item.get("homepage", "")).strip(),
                    download_link=str(item.get("download_link", "")).strip(),
                    tags=[str(tag).strip() for tag in (item.get("tags", []) if isinstance(item.get("tags", []), list) else []) if str(tag).strip()],
                    relations={str(key).strip(): str(value).strip() for key, value in (item.get("relations", {}) if isinstance(item.get("relations", {}), dict) else {}).items() if str(key).strip() and str(value).strip()},
                ) for item in catalog.get("plugins", []) if isinstance(item, dict) and str(item.get("id", "")).strip()
            ]
        return self._plugin_entries or []

    def list_themes(self, *, load_if_needed: bool = True) -> list[CatalogThemeEntry]:
        if self._theme_entries is None or (load_if_needed and self._catalog is None):
            catalog = self.load_catalog() if load_if_needed else (self._catalog or {})
            self._theme_entries = [
                CatalogThemeEntry(
                    id=str(item.get("id", "")).strip(), name=str(item.get("name", "")).strip(),
                    description=str(item.get("description", "")).strip(), author=str(item.get("author", "")).strip(),
                    version=str(item.get("version", "")).strip(), icon=str(item.get("icon", "")).strip(),
                    homepage=str(item.get("homepage", "")).strip(), download_link=str(item.get("download_link", "")).strip(),
                    tags=[str(tag).strip() for tag in (item.get("tags", []) if isinstance(item.get("tags", []), list) else []) if str(tag).strip()],
                ) for item in catalog.get("themes", []) if isinstance(item, dict) and str(item.get("id", "")).strip()
            ]
        return self._theme_entries or []

    def list_entries(self, *, load_if_needed: bool = True) -> list[CatalogPluginEntry]:
        """Compatibility alias for older plugin catalog callers."""
        return self.list_plugins(load_if_needed=load_if_needed)

    def get_entry(self, plugin_id: str, *, load_if_needed: bool = True) -> CatalogPluginEntry | None:
        return next((entry for entry in self.list_plugins(load_if_needed=load_if_needed) if entry.id == plugin_id), None)

    def get_theme(self, theme_id: str, *, load_if_needed: bool = True) -> CatalogThemeEntry | None:
        return next((entry for entry in self.list_themes(load_if_needed=load_if_needed) if entry.id == theme_id), None)

    def _try_fetch_catalog(self) -> dict | None:
        session = getattr(self.app_state, "network_session", None)
        if not session:
            return None
        catalog: dict[str, list] = dict(self._catalog or {})
        for key, url in (("plugins", PLUGIN_CATALOG_URL), ("themes", THEME_CATALOG_URL)):
            try:
                response = session.get(url, timeout=5)
                response.raise_for_status()
                data = response.json() or {}
                entries = data.get(key, []) if isinstance(data, dict) else data
                if isinstance(entries, list):
                    catalog[key] = entries
            except Exception as error:
                logger.warning("CatalogService: %s catalog fetch failed (%s): %s", key, type(error).__name__, error)
        return catalog or None

    def _invalidate_cache(self) -> None:
        self._plugin_entries = None
        self._theme_entries = None
