"""Qt5/Qt6 compatible dock UI for QGIS 3.44 and 4.x.

Intentionally relies only on QGIS, PyQt and the Python standard library.
"""

import gzip
import os

from qgis.PyQt.QtCore import QDate, QEventLoop, QSettings, Qt, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDockWidget, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget
)
from qgis.core import (
    QgsDataSourceUri, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsApplication, QgsNetworkAccessManager, QgsProject, QgsRasterLayer, QgsVectorLayer, Qgis
)

from .catalog import THEMES, read_capabilities, read_local
from .temporal import MIN_YEAR, MAX_YEAR, choose_fields, ogr_filter, parse_date, with_year
from .wms import (add_query_params, as_uri, endpoint_from_token,
                  token_from_legacy_url, wms_endpoint_for_date)
from .playback import (next_year, playback_scale_allowed, PLAYBACK_INTERVAL_MS,
                       RENDER_POLL_MS, RENDER_TIMEOUT_MS)
from .wms_gdal import persist_description
from .network_compat import is_network_error


SETTINGS_PREFIX = 'DIGDAG_plugin_v01/'



class DigDagDock(QDockWidget):
    def __init__(self, iface):
        super().__init__('DIGDAG – historiske grænser', iface.mainWindow())
        self.iface = iface
        self.settings = QSettings()
        self.catalog = {}
        self.source_signature = None
        self.active_signature = None
        self.active_group = None
        self.active_layers = []
        self.active_kind = None
        self._loading = False
        self._playing = False
        self._play_changing = False
        self._render_wait_ms = 0
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._selection_changed)
        self._play_timer = QTimer(self)
        self._play_timer.setSingleShot(True)
        self._play_timer.timeout.connect(self._advance_playback)
        self.iface.mapCanvas().scaleChanged.connect(self._scale_changed)
        self.setObjectName('DIGDAG_DockPanel_v01')
        self.setMinimumWidth(330)
        self.resize(405, 690)
        self._build_ui()
        self._set_source_mode()
        self._read_settings()
        self._show_status('WMS er valgt som standard. Klik »Find DIGDAG-lag« for at begynde.')

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame if hasattr(QFrame, "Shape") else QFrame.NoFrame)
        content = QWidget()
        self.layout_main = QVBoxLayout(content)
        self.layout_main.setContentsMargins(10, 10, 10, 10)
        self.layout_main.setSpacing(10)
        scroll.setWidget(content)
        self.setWidget(scroll)

        heading = QLabel('DIGDAG  ·  1660–2010')
        heading.setStyleSheet('font-weight: 600; font-size: 17px;')
        self.layout_main.addWidget(heading)

        # Expandable source controls: collapsed by default, WMS selected.
        self.sources_toggle = QToolButton()
        self.sources_toggle.setText('Datakilde  ·  WMS')
        self.sources_toggle.setCheckable(True)
        self.sources_toggle.setChecked(False)
        self.sources_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.sources_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sources_toggle.setStyleSheet('QToolButton { font-weight: 600; padding: 5px; border: 0; }')
        self.layout_main.addWidget(self.sources_toggle)

        self.sources_content = QFrame()
        sv = QVBoxLayout(self.sources_content)
        sv.setContentsMargins(4, 0, 4, 5)
        self.mode = QComboBox()
        self.mode.addItem('WMS – Dataforsyningen', 'wms')
        self.mode.addItem('Lokal vektor (SHP / ZIP)', 'local')
        sv.addWidget(self.mode)

        self.local_section = QWidget()
        local_l = QVBoxLayout(self.local_section)
        local_l.setContentsMargins(0, 3, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r'Z:\_Vektorkort\DigDag')
        self.path_edit.setToolTip('Overordnet DIGDAG-mappe, en undermappe eller én .zip-fil.')
        local_l.addWidget(self.path_edit)
        browse_row = QHBoxLayout()
        self.browse_dir = QPushButton('Vælg mappe…')
        self.browse_zip = QPushButton('Vælg ZIP…')
        browse_row.addWidget(self.browse_dir)
        browse_row.addWidget(self.browse_zip)
        local_l.addLayout(browse_row)
        sv.addWidget(self.local_section)

        self.wms_section = QWidget()
        wms_l = QFormLayout(self.wms_section)
        wms_l.setContentsMargins(0, 3, 0, 0)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText('Indsæt token fra Dataforsyningen')
        self.token_edit.setToolTip('Kun dit token – ikke hele WMS-adressen. Gemmes i QGIS-indstillinger.')
        self.token_edit.setClearButtonEnabled(True)
        wms_l.addRow('Token:', self.token_edit)
        sv.addWidget(self.wms_section)

        self.scan_button = QPushButton('Find DIGDAG-lag')
        self.sources_content.setVisible(False)
        self.layout_main.addWidget(self.sources_content)
        # Discovery remains accessible while the source settings are collapsed.
        self.layout_main.addWidget(self.scan_button)

        choose = QGroupBox('Administrativ inddeling')
        form = QFormLayout(choose)
        self.theme_combo = QComboBox()
        self.subtype_combo = QComboBox()
        form.addRow('Tema:', self.theme_combo)
        form.addRow('Undertema:', self.subtype_combo)
        self.layout_main.addWidget(choose)

        timebox = QGroupBox('Tidspunkt')
        t = QVBoxLayout(timebox)
        t_row = QHBoxLayout()
        t_row.setSpacing(6)
        t_row.addWidget(QLabel('År'))
        self.date_text = QLineEdit('1900')
        self.date_text.setPlaceholderText('År eller YYYY-MM-DD')
        self.date_text.setToolTip('Skriv fx 1750, 1850-09 eller 1900-01-01.')
        t_row.addWidget(self.date_text, stretch=3)
        t_row.addWidget(QLabel('Dato'))
        self.date_picker = QDateEdit()
        self.date_picker.setDisplayFormat('dd.MM.yyyy')
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setMinimumDate(QDate(MIN_YEAR, 1, 1))
        self.date_picker.setMaximumDate(QDate(MAX_YEAR, 12, 31))
        self.date_picker.setDate(QDate(1900, 1, 1))
        t_row.addWidget(self.date_picker, stretch=2)
        t.addLayout(t_row)
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel(str(MIN_YEAR)))
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setMinimum(MIN_YEAR)
        self.timeline.setMaximum(MAX_YEAR)
        self.timeline.setSingleStep(1)
        self.timeline.setPageStep(10)
        self.timeline.setTracking(True)
        self.timeline.setValue(1900)
        slider_row.addWidget(self.timeline, stretch=1)
        slider_row.addWidget(QLabel(str(MAX_YEAR)))
        t.addLayout(slider_row)
        self.current_label = QLabel('01.01.1900')
        self.current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_label.setStyleSheet('font-weight: 600;')
        t.addWidget(self.current_label)
        self.auto_apply = QCheckBox('Opdater kortet automatisk ved valg')
        self.auto_apply.setChecked(True)
        t.addWidget(self.auto_apply)
        play_row = QHBoxLayout()
        self.play_button = QPushButton('▶  Afspil · 10 år')
        self.play_button.setToolTip('Afspiller i tiårstrin ved målestok mellem 1:1 og 1:100.000. '                                     'Kortets zoomniveau ændres ikke.')
        play_row.addWidget(self.play_button)
        t.addLayout(play_row)
        play_hint = QLabel('Afspilning: 10-årstrin · 1:1–1:100.000 · zoom ændres ikke')
        play_hint.setStyleSheet('color: palette(mid); font-size: 10px;')
        play_hint.setWordWrap(True)
        t.addWidget(play_hint)
        self.layout_main.addWidget(timebox)

        self.apply_button = QPushButton('Vis / opdater DIGDAG på kortet')
        self.apply_button.setDefault(True)
        self.layout_main.addWidget(self.apply_button)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet('padding: 7px; border: 1px solid palette(mid); border-radius: 3px;')
        self.layout_main.addWidget(self.status)
        self.layout_main.addStretch()

        self.sources_toggle.toggled.connect(self._toggle_sources)
        self.mode.currentIndexChanged.connect(self._set_source_mode)
        self.mode.currentIndexChanged.connect(self._source_changed)
        self.path_edit.editingFinished.connect(self._source_changed)
        self.token_edit.editingFinished.connect(self._source_changed)
        self.browse_dir.clicked.connect(self._choose_dir)
        self.browse_zip.clicked.connect(self._choose_zip)
        self.scan_button.clicked.connect(self.scan)
        self.theme_combo.currentIndexChanged.connect(self._fill_subtypes)
        self.subtype_combo.currentIndexChanged.connect(self._selection_changed)
        self.date_text.editingFinished.connect(self._typed_date)
        self.date_picker.dateChanged.connect(self._picked_date)
        self.timeline.valueChanged.connect(self._slider_moved)
        self.timeline.sliderReleased.connect(self._slider_released)
        self.apply_button.clicked.connect(self.apply)
        self.play_button.clicked.connect(self._toggle_playback)

    def _show_status(self, message):
        self.status.setText(message)

    def _read_settings(self):
        default_path = r'Z:\_Vektorkort\DigDag'
        saved = self.settings.value(SETTINGS_PREFIX + 'path', default_path)
        self.path_edit.setText(saved or default_path)
        # Migrate the v0.2 full URL into a token-only input, once.
        token = self.settings.value(SETTINGS_PREFIX + 'token', None)
        if token is None:
            old_url = self.settings.value(SETTINGS_PREFIX + 'wms_url', '')
            token = token_from_legacy_url(old_url)
        self.token_edit.setText(str(token or ''))
        self.auto_apply.setChecked(str(self.settings.value(SETTINGS_PREFIX + 'auto', 'true')).lower() == 'true')
        self._set_source_mode()

    def _persist_settings(self):
        self.settings.setValue(SETTINGS_PREFIX + 'path', self.path_edit.text().strip())
        # Dataforsyningen token was already persisted in QGIS settings in v0.2.
        self.settings.setValue(SETTINGS_PREFIX + 'token', self.token_edit.text().strip())
        self.settings.remove(SETTINGS_PREFIX + 'wms_url')
        self.settings.setValue(SETTINGS_PREFIX + 'auto', 'true' if self.auto_apply.isChecked() else 'false')

    def _endpoint(self):
        if self.mode.currentData() == 'local':
            return self.path_edit.text().strip()
        return endpoint_from_token(self.token_edit.text())

    def _toggle_sources(self, expanded):
        self.sources_content.setVisible(expanded)
        self.sources_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded
                                         else Qt.ArrowType.RightArrow)

    def _set_source_mode(self, *_args):
        local = self.mode.currentData() == 'local'
        self.local_section.setVisible(local)
        self.wms_section.setVisible(not local)
        self.sources_toggle.setText('Datakilde  ·  ' + ('Lokal vektor' if local else 'WMS'))

    def _source_changed(self, *_args):
        self._stop_playback()
        self._apply_timer.stop()
        self.catalog = {}
        self.source_signature = None
        self.theme_combo.clear()
        self.subtype_combo.clear()

    def _choose_dir(self):
        base = self.path_edit.text()
        if not os.path.isdir(base):
            base = os.path.expanduser('~')
        folder = QFileDialog.getExistingDirectory(self, 'Vælg DIGDAG-mappe', base)
        if folder:
            self.path_edit.setText(folder)
            self._source_changed()
            self.scan()

    def _choose_zip(self):
        file_path, _ = QFileDialog.getOpenFileName(self, 'Vælg DIGDAG-ZIP',
            os.path.expanduser('~'), 'ZIP-arkiver (*.zip)')
        if file_path:
            self.path_edit.setText(file_path)
            self._source_changed()
            self.scan()

    def scan(self):
        if self._loading:
            return
        self._stop_playback()
        kind = self.mode.currentData()
        endpoint = self._endpoint()
        if not endpoint:
            QMessageBox.warning(self, 'DIGDAG', 'Angiv først en datakilde.')
            return
        self._loading = True
        self.scan_button.setEnabled(False)
        self._show_status('Finder lag…')
        try:
            if kind == 'local':
                result = read_local(endpoint)
            else:
                result = read_capabilities(self._capabilities_xml(endpoint))
            if not result:
                raise ValueError('Ingen shapefiler eller WMS-lag fundet i den valgte datakilde.')
            self.catalog = result
            self.source_signature = (kind, endpoint)
            self.theme_combo.blockSignals(True)
            self.theme_combo.clear()
            for theme in THEMES:
                if theme in self.catalog:
                    total = len(self.catalog[theme])
                    self.theme_combo.addItem('{} ({})'.format(theme, total), theme)
            self.theme_combo.blockSignals(False)
            self._fill_subtypes()
            self._persist_settings()
            # Keep source options out of the way after successful discovery.
            self.sources_toggle.setChecked(False)
            count = sum(map(len, result.values()))
            self._show_status('Fundet {} undertemaer fordelt på {} hovedtemaer.\nVælg en inddeling og et tidspunkt.'
                              .format(count, len(result)))
        except Exception as exc:
            self._show_status('Kunne ikke finde lag: {}'.format(exc))
            QMessageBox.warning(self, 'DIGDAG – datakilde', str(exc))
        finally:
            self._loading = False
            self.scan_button.setEnabled(True)

    def _capabilities_xml(self, endpoint):
        url = add_query_params(endpoint, SERVICE='WMS', REQUEST='GetCapabilities', VERSION='1.3.0')
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept-Encoding', b'identity')
        reply = QgsNetworkAccessManager.instance().get(request)
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        reply.finished.connect(loop.quit)
        timer.timeout.connect(loop.quit)
        timer.start(15000)
        if not reply.isFinished():
            loop.exec()
        timed_out = not reply.isFinished()
        if timed_out:
            reply.abort()
        timer.stop()
        if timed_out:
            raise ValueError('WMS svarede ikke inden for 15 sekunder.')
        try:
            # PyQt5 exposes Qt enums as int-like values, whereas PyQt6
            # returns a scoped NetworkError enum (not convertible with int()).
            error = reply.error()
            if is_network_error(error):
                raise ValueError('WMS-netværksfejl: ' + reply.errorString())
            data = bytes(reply.readAll())
            if data.startswith(b'\x1f\x8b'):
                data = gzip.decompress(data)
            return data
        finally:
            reply.deleteLater()

    def _fill_subtypes(self, *_args):
        theme = self.theme_combo.currentData()
        self.subtype_combo.blockSignals(True)
        self.subtype_combo.clear()
        if theme and theme in self.catalog:
            names = list(self.catalog[theme])
            names.sort(key=lambda s: s.lower())
            # Present common church divisions first, then others alphabetically.
            if theme == 'Kirkelig':
                wanted = {'sogn': 0, 'pastorat': 1, 'provsti': 2, 'stift': 3}
                names.sort(key=lambda n: (wanted.get(n.lower(), 100), n.lower()))
            for subtype in names:
                items = self.catalog[theme][subtype]
                suffix = ' · {} dele'.format(len(items)) if len(items) > 1 else ''
                self.subtype_combo.addItem(subtype + suffix, subtype)
        self.subtype_combo.blockSignals(False)
        # Do not reload unless there was an earlier map; scan alone is safe.
        self._selection_changed()

    def _selection_changed(self, *_args):
        if self._playing and not self._play_changing:
            self._stop_playback()
        self._apply_timer.stop()
        if self.auto_apply.isChecked() and self.active_group is not None and self.catalog:
            self.apply()

    def _schedule_auto_apply(self):
        if not self._playing and self.auto_apply.isChecked() and self.active_group is not None:
            # valueChanged fires on clicks, arrow keys AND dragging.
            # Debounce drag updates to avoid flooding Dataforsyningen.
            self._apply_timer.start(160)

    def _date(self, warn=True):
        try:
            return parse_date(self.date_text.text())[0]
        except ValueError as exc:
            if warn:
                QMessageBox.warning(self, 'DIGDAG – tidspunkt', str(exc))
            return None

    def _sync_date(self, d):
        self.timeline.blockSignals(True)
        self.timeline.setValue(d.year)
        self.timeline.blockSignals(False)
        self.date_picker.blockSignals(True)
        self.date_picker.setDate(QDate(d.year, d.month, d.day))
        self.date_picker.blockSignals(False)
        self.current_label.setText(d.strftime('%d.%m.%Y'))

    def _typed_date(self):
        self._stop_playback()
        d = self._date()
        if d:
            self._sync_date(d)
            self._selection_changed()

    def _picked_date(self, qd):
        if not self._play_changing:
            self._stop_playback()
        if not qd.isValid():
            return
        self.date_text.setText('{:04}-{:02}-{:02}'.format(qd.year(), qd.month(), qd.day()))
        self._sync_date(self._date(warn=False))
        self._selection_changed()

    def _slider_moved(self, year):
        if not self._play_changing:
            self._stop_playback()
        old = self._date(warn=False)
        if old is None:
            old, _ = parse_date('1900')
        d = with_year(old, year)
        # Timeline changes only the year; preserve day / month when available.
        current_text = self.date_text.text().strip()
        try:
            _, precision = parse_date(current_text)
        except ValueError:
            precision = 'year'
        if precision == 'year':
            new_text = str(d.year)
        elif precision == 'month':
            new_text = d.strftime('%Y-%m')
        else:
            new_text = d.isoformat()
        self.date_text.setText(new_text)
        self._sync_date(d)
        if not self._play_changing:
            self._schedule_auto_apply()

    def _slider_released(self):
        # Flush any debounced update when drag ends.
        if not self._playing:
            self._selection_changed()

    def apply(self):
        if self._loading:
            return False
        d = self._date()
        if not d:
            return False
        theme = self.theme_combo.currentData()
        subtype = self.subtype_combo.currentData()
        kind = self.mode.currentData()
        endpoint = self._endpoint()
        if self.source_signature != (kind, endpoint) or not self.catalog:
            self._show_status('Klik på »Find DIGDAG-lag« efter ændring af datakilde.')
            return False
        if not theme or not subtype:
            self._show_status('Vælg både tema og undertema.')
            return False
        sources = self.catalog[theme][subtype]
        signature = (kind, endpoint, theme, subtype)
        self._loading = True
        self.apply_button.setEnabled(False)
        try:
            if kind == 'local':
                self._apply_local(sources, signature, d, theme, subtype)
            else:
                self._apply_wms(sources, signature, d, theme, subtype)
            return True
        except Exception as exc:
            self._show_status('Lag ikke opdateret: ' + str(exc))
            if self._playing:
                self._stop_playback()
            QMessageBox.warning(self, 'DIGDAG – kortlag', str(exc))
            return False
        finally:
            self._loading = False
            self.apply_button.setEnabled(True)

    @staticmethod
    def _filter_layer(layer, d):
        fields = list(layer.fields())
        start, end = choose_fields([f.name() for f in fields])
        if not start or not end:
            raise ValueError('{}: finder ikke både FRA og TIL. Felter: {}'.format(
                layer.name(), ', '.join(f.name() for f in fields)))
        field_map = {f.name(): f for f in fields}
        def is_numeric(name):
            ft = str(field_map[name].typeName()).lower()
            return ft in ('integer', 'integer64', 'int', 'long', 'smallint', 'real', 'double', 'numeric')
        expression = ogr_filter(d, start, end, is_numeric(start), is_numeric(end))
        if not layer.setSubsetString(expression):
            raise ValueError('{}: OGR afviste tidsfilteret: {}'.format(layer.name(), expression))
        return expression

    @staticmethod
    def _style(layer):
        geom = int(layer.geometryType())
        if geom == 2:
            symbol = QgsFillSymbol.createSimple({
                'color': '62,141,199,42',
                'outline_color': '22,76,125,255',
                'outline_width': '0.38',
                'outline_width_unit': 'MM',
            })
        elif geom == 1:
            symbol = QgsLineSymbol.createSimple({
                'line_color': '22,76,125,255', 'line_width': '0.42',
                'line_width_unit': 'MM',
            })
        else:
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle', 'color': '52,123,181,175', 'size': '1.4',
                'outline_color': '22,76,125,255',
            })
        if layer.renderer() is not None:
            layer.renderer().setSymbol(symbol)
        for f in ('NAVN', 'navn', 'Navn'):
            if layer.fields().indexOf(f) >= 0:
                layer.setDisplayExpression('"{}"'.format(f))
                break

    def _apply_local(self, sources, signature, d, theme, subtype):
        project = QgsProject.instance()
        if self.active_signature == signature and self.active_kind == 'local' and self.active_layers:
            # Fast path: change the existing shapefile's provider SQL filter in place.
            expressions = []
            old = [layer.subsetString() for layer in self.active_layers]
            try:
                for layer in self.active_layers:
                    expressions.append(self._filter_layer(layer, d))
            except Exception:
                for layer, previous in zip(self.active_layers, old):
                    layer.setSubsetString(previous)
                raise
            self._rename_active(theme, subtype, d)
            self.iface.mapCanvas().refresh()
            self._show_status('{} / {}\n{} · {} lokale del-lag opdateret.'.format(
                theme, subtype, d.isoformat(), len(self.active_layers)))
            return

        layers = []
        for src in sources:
            layer = QgsVectorLayer(src.path, 'DIGDAG – ' + src.label, 'ogr')
            if not layer.isValid():
                raise ValueError('Kan ikke åbne {}. ZIP? Prøv at pakke ZIP-filen ud.'.format(src.path))
            self._filter_layer(layer, d)
            self._style(layer)
            layer.setCustomProperty('digdag_plugin/managed', True)
            layers.append(layer)
        self._install_layers(layers, signature, 'local', theme, subtype, d)
        zip_count = sum(1 for source in sources if source.compressed)
        note = ' Fra ZIP – udpakning anbefales for hastighed.' if zip_count else ''
        self._show_status('{} / {}\n{} · {} lokale del-lag indlæst.{}'.format(
            theme, subtype, d.isoformat(), len(layers), note))

    def _wms_source(self, endpoint, name, day):
        """Choose a capabilities-free GDAL WMS source on Qt6/QGIS 4.

        QGIS 4.2 may reject gzip-compressed GetCapabilities; the GDAL WMS
        driver does not request capabilities, only dated GetMap images.
        """
        if Qgis.QGIS_VERSION_INT >= 40000:
            return (persist_description(QgsApplication.qgisSettingsDirPath(),
                                        endpoint, name, day), 'gdal')
        dated_endpoint = wms_endpoint_for_date(endpoint, day, 'time')
        return (as_uri(dated_endpoint, name, QgsDataSourceUri), 'wms')

    def _apply_wms(self, sources, signature, d, theme, subtype):
        entry = sources[0]
        uri, provider = self._wms_source(self._endpoint(), entry.name, d)
        project = QgsProject.instance()
        if (self.active_signature == signature and self.active_kind == 'wms'
                and len(self.active_layers) == 1
                and project.mapLayer(self.active_layers[0].id()) is not None):
            layer = self.active_layers[0]
            original_uri = layer.source()
            old_provider = layer.providerType()
            if uri != original_uri or provider != old_provider:
                layer.setDataSource(uri, layer.name(), provider)
                if not layer.isValid():
                    # Recover a known-good previous frame without removing the layer.
                    layer.setDataSource(original_uri, layer.name(), old_provider)
                    raise ValueError('Kunne ikke opdatere DIGDAG WMS til den valgte dato.')
            self._rename_active(theme, subtype, d)
            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
        else:
            layer = QgsRasterLayer(uri, 'DIGDAG – ' + entry.subtype, provider)
            if not layer.isValid():
                raise ValueError('QGIS kunne ikke oprette WMS-laget via {}. '
                                 'Kontrollér token og lagnavn.'.format(provider))
            layer.setCustomProperty('digdag_plugin/managed', True)
            self._install_layers([layer], signature, 'wms', theme, subtype, d)
        self._show_status('{} / {}\n{} · WMS time={} ({}).'
                          .format(theme, subtype, d.isoformat(), d.isoformat(), provider))

    def _install_layers(self, layers, signature, kind, theme, subtype, d):
        project = QgsProject.instance()
        # Keep existing group unchanged until all new layers are validated.
        self._remove_active()
        root = project.layerTreeRoot()
        name = 'DIGDAG | {} | {} | {}'.format(theme, subtype, d.isoformat())
        group = root.insertGroup(0, name)
        for layer in layers:
            project.addMapLayer(layer, False)
            group.addLayer(layer)
        self.active_group = group
        self.active_layers = layers
        self.active_kind = kind
        self.active_signature = signature
        self.iface.mapCanvas().refresh()

    def _rename_active(self, theme, subtype, d):
        if self.active_group is not None:
            self.active_group.setName('DIGDAG | {} | {} | {}'.format(
                theme, subtype, d.isoformat()))

    def _remove_active(self):
        project = QgsProject.instance()
        # Only delete nodes/layers this specific plugin instance created.
        for layer in self.active_layers:
            if project.mapLayer(layer.id()) is not None:
                project.removeMapLayer(layer.id())
        if self.active_group is not None:
            try:
                if self.active_group.parent() is not None:
                    self.active_group.parent().removeChildNode(self.active_group)
            except RuntimeError:
                pass
        self.active_layers = []
        self.active_group = None
        self.active_signature = None
        self.active_kind = None

    def _toggle_playback(self):
        if self._playing:
            self._stop_playback()
            return
        if not self.catalog or not self.theme_combo.currentData() or not self.subtype_combo.currentData():
            self._show_status('Find DIGDAG-lag og vælg et tema før afspilning.')
            return
        if self._date() is None:
            return
        scale = self.iface.mapCanvas().scale()
        if not playback_scale_allowed(scale):
            self._show_status('Afspilning kræver målestok 1:1–1:100.000. '
                              'Zoom selv ind til 1:100.000 eller tættere. Zoom ændres ikke.')
            return
        self._apply_timer.stop()
        if self.timeline.value() >= MAX_YEAR:
            self._play_changing = True
            try:
                self.timeline.setValue(MIN_YEAR)
            finally:
                self._play_changing = False
        self._playing = True
        self._render_wait_ms = 0
        self.play_button.setText('⏸  Pause')
        self.play_button.setToolTip('Pause ved den viste dato.')
        self._render_playback_frame()

    def _render_playback_frame(self):
        if not self._playing:
            return
        # Apply the actual time immediately and then allow time for the map to
        # draw. Unlike v0.3, no advance depends on a mapCanvasRefreshed signal,
        # which can arrive while apply() is still in progress or be omitted.
        if not self.apply():
            self._stop_playback()
            return
        self._render_wait_ms = 0
        self._play_timer.start(PLAYBACK_INTERVAL_MS)

    def _advance_playback(self):
        if not self._playing:
            return
        if not playback_scale_allowed(self.iface.mapCanvas().scale()):
            self._stop_playback()
            self._show_status('Afspilningen er sat på pause: målestokken skal være '
                              'mellem 1:1 og 1:100.000.')
            return
        # On slower WMS connections wait for drawing rather than starting a
        # second frame. Still enforce a finite timeout to avoid frozen playback.
        if self.iface.mapCanvas().isDrawing():
            self._render_wait_ms += RENDER_POLL_MS
            if self._render_wait_ms >= RENDER_TIMEOUT_MS:
                self._stop_playback()
                self._show_status('Afspilningen er sat på pause, fordi korttegningen '
                                  'tog mere end 20 sekunder. Prøv igen.')
                return
            self._play_timer.start(RENDER_POLL_MS)
            return
        self._render_wait_ms = 0
        year = next_year(self.timeline.value())
        if year is None:
            self._stop_playback()
            self._show_status('Afspilning afsluttet ved år {}.'.format(MAX_YEAR))
            return
        self._play_changing = True
        try:
            self.timeline.setValue(year)
        finally:
            self._play_changing = False
        self._render_playback_frame()

    def _scale_changed(self, scale):
        if self._playing and not playback_scale_allowed(scale):
            self._stop_playback()
            self._show_status('Afspilningen er sat på pause uden at ændre zoom: '
                              'vælg målestok 1:1–1:100.000.')

    def _stop_playback(self):
        self._playing = False
        self._render_wait_ms = 0
        self._play_timer.stop()
        self.play_button.setText('▶  Afspil · 10 år')
        self.play_button.setToolTip('Afspiller i tiårstrin ved målestok mellem '
                                    '1:1 og 1:100.000 uden at ændre zoom.')

    def closeEvent(self, event):
        # Closing the panel must not remove its map layers.
        self._stop_playback()
        self._apply_timer.stop()
        self._persist_settings()
        super().closeEvent(event)
