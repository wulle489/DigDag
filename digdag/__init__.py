"""QGIS plugin entry point."""


def classFactory(iface):
    from .digdag_plugin import DigDagPlugin
    return DigDagPlugin(iface)
