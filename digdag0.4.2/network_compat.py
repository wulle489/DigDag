"""Compatibility helpers for QGIS 3.x / PyQt5 and QGIS 4.x / PyQt6."""


def is_network_error(error):
    """Handle int-like Qt5 errors and PyQt6 QNetworkReply.NetworkError enums."""
    return getattr(error, 'value', error) != 0
