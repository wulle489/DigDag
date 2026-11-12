"""QGIS menu and dock lifecycle."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction, QIcon
import os
from .dock import DigDagDock


class DigDagPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        self.action = QAction(QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg')),
                              'DIGDAG – historiske grænser', self.iface.mainWindow())
        self.action.triggered.connect(self.show_panel)
        self.iface.addPluginToMenu('&DIGDAG', self.action)
        self.iface.addToolBarIcon(self.action)

    def show_panel(self):
        if self.dock is None:
            self.dock = DigDagDock(self.iface)
            self.dock.destroyed.connect(self._dock_destroyed)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def _dock_destroyed(self, *_args):
        self.dock = None

    def unload(self):
        if self.dock is not None:
            # Closing the dock does not delete user-visible map layers.
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu('&DIGDAG', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
