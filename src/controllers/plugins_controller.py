"""Compatibility import for the renamed catalog controller."""

from controllers.catalog_controller import CatalogController
from ui.dialogs.plugin_details_dialog import PluginDetailsDialog


class PluginsController(CatalogController):
    """Compatibility facade for extensions that still import the old name."""

    def show_plugin_details(self, plugin_id: str) -> None:
        import controllers.catalog_controller as catalog_module

        original = catalog_module.PluginDetailsDialog
        catalog_module.PluginDetailsDialog = PluginDetailsDialog
        try:
            super().show_plugin_details(plugin_id)
        finally:
            catalog_module.PluginDetailsDialog = original
