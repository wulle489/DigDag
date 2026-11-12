"""Discover DigDag shapefiles or WMS layers without loading full geometries."""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import unicodedata
import zipfile
# defusedxml is used where available.  QGIS' Qt XML stream reader is a
# self-contained fallback so the plugin needs no external Python packages.
try:
    from defusedxml import ElementTree as _safe_etree
    from defusedxml.common import DefusedXmlException
except ImportError:
    _safe_etree = None
    DefusedXmlException = ValueError


THEMES = (
    'Kirkelig',
    'Amtslig / regional',
    'Geografisk',
    'Kommunal',
    'Retslig',
    'Politivæsen',
    'Øvrige',
)

PREFIXES = (
    ('amtslig', 'Amtslig / regional'),
    ('regional', 'Amtslig / regional'),
    ('geografisk', 'Geografisk'),
    ('kirkelig', 'Kirkelig'),
    ('kommunal', 'Kommunal'),
    ('retslig', 'Retslig'),
    ('politi', 'Politivæsen'),
    ('ovrige', 'Øvrige'),
)


def ascii_key(value):
    value = str(value).lower().replace('æ', 'ae').replace('ø', 'o').replace('å', 'a')
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '_', value).strip('_')


def theme_for_path(path):
    # Prioritise enclosing directory / zip name over the individual shapefile.
    parts = re.split(r'[\\/]+', str(path))
    for part in parts[:-1]:
        key = ascii_key(part)
        for prefix, theme in PREFIXES:
            if key.startswith(prefix):
                return theme
    key = ascii_key(parts[-1]) if parts else ''
    for prefix, theme in PREFIXES:
        if key.startswith(prefix):
            return theme
    return 'Øvrige'


def nice_title(raw):
    raw = re.sub(r'(?i)(?:[_-](?:sjl|jyl|fyn|bornholm|bh|oerne|oer))$', '', raw)
    raw = re.sub(r'(?i)(?:[_-](?:polygon|polygone|poly))$', '', raw)
    text = re.sub(r'[_-]+', ' ', raw).strip()
    return text[:1].upper() + text[1:]


def subtype_key(stem):
    stem = re.sub(r'(?i)(?:[_-](?:sjl|jyl|fyn|bornholm|bh|oerne|oer))$', '', stem)
    stem = re.sub(r'(?i)(?:[_-](?:polygon|polygone|poly))$', '', stem)
    return ascii_key(stem)


@dataclass(frozen=True)
class LocalSource:
    theme: str
    subtype: str
    path: str
    label: str
    compressed: bool = False


def read_local(root):
    """Returns {theme: {display subtype: [LocalSource]}}.

    For a folder, extracted .shp files win over zip archives in each main
    theme, because reading a multi-GB ZIP using /vsizip is much slower.
    """
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.exists(root):
        raise ValueError('Mappen eller ZIP-filen findes ikke: ' + root)
    shp_items, zip_items = [], []
    if os.path.isfile(root):
        if root.lower().endswith('.zip'):
            zip_items = [root]
        elif root.lower().endswith('.shp'):
            shp_items = [root]
        else:
            raise ValueError('Vælg en mappe med shapefiler eller en .zip-fil.')
    else:
        for base, folders, files in os.walk(root):
            folders[:] = [f for f in folders if f not in ('__pycache__', '.git')]
            for name in files:
                full = os.path.join(base, name)
                if name.lower().endswith('.shp'):
                    shp_items.append(full)
                elif name.lower().endswith('.zip'):
                    zip_items.append(full)

    # Index by normalized subtype key, so e.g. stift_sjl / stift_jyl group.
    groups = {}
    extracted_themes = set()
    for path in sorted(shp_items, key=str.lower):
        theme = theme_for_path(path)
        extracted_themes.add(theme)
        stem = Path(path).stem
        key = (theme, subtype_key(stem))
        groups.setdefault(key, []).append(LocalSource(theme, nice_title(stem), path,
                                                        os.path.basename(path)))

    for path in sorted(zip_items, key=str.lower):
        theme = theme_for_path(path)
        if theme in extracted_themes:
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if not member.lower().endswith('.shp') or member.endswith('/'):
                        continue
                    stem = Path(member).stem
                    src = '/vsizip/' + path.replace('\\', '/') + '/' + member
                    key = (theme, subtype_key(stem))
                    groups.setdefault(key, []).append(LocalSource(
                        theme, nice_title(stem), src, os.path.basename(member), True))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError('Kunne ikke læse ZIP: {} ({})'.format(path, exc)) from exc

    by_theme = {}
    for (theme, key), items in sorted(groups.items()):
        # Use a stable display name independent of file / region ordering.
        label = nice_title(key)
        by_theme.setdefault(theme, {})[label] = items
    return by_theme


def guess_wms_theme(title, ancestors=()):
    context = ascii_key(' '.join(ancestors))
    for prefix, theme in PREFIXES:
        if re.search(r'(?:^|_)' + prefix + r'(?:_|$)', context):
            return theme
    name = ascii_key(title)
    if any(word in name for word in ('pastorat', 'provsti', 'stift', 'sogn')):
        return 'Kirkelig'
    if any(word in name for word in ('amtskreds', 'region', 'amt')):
        return 'Amtslig / regional'
    if any(word in name for word in ('geografisk', 'ejerlav', 'landsdel')):
        return 'Geografisk'
    if 'politi' in name:
        return 'Politivæsen'
    if any(word in name for word in ('retskreds', 'birk', 'ret', 'herred')):
        return 'Retslig'
    if any(word in name for word in ('kommune', 'kommunal', 'koebstad')):
        return 'Kommunal'
    return 'Øvrige'


@dataclass(frozen=True)
class WmsSource:
    theme: str
    subtype: str
    name: str
    has_time: bool
    description: str = ''


def _children(parent, tag):
    return [e for e in parent if e.tag.rsplit('}', 1)[-1] == tag]


def _one_text(parent, tag):
    for e in _children(parent, tag):
        if e.text:
            return e.text.strip()
    return ''


class _XmlNode:
    """Small, ElementTree-compatible representation for Qt's safe XML stream."""

    def __init__(self, tag, attributes):
        self.tag = tag
        self.attrib = attributes
        self.text = ''
        self.children = []

    def __iter__(self):
        return iter(self.children)

    def itertext(self):
        if self.text:
            yield self.text
        for child in self.children:
            yield from child.itertext()


def _qt_string(value):
    """Convert both Qt5 QStringRef and Qt6 QStringView to Python strings."""
    return str(value.toString() if hasattr(value, 'toString') else value)


def _parse_with_qt(xml_bytes):
    """Read XML using Qt without resolving external entities or accepting DTDs.

    QXmlStreamReader ships with QGIS on Qt5 and Qt6. Unlike the standard XML
    DOM parser, it does not download external entities; we reject all DTDs and
    entity references explicitly. Depth/node caps guard against resource use.
    """
    from qgis.PyQt.QtCore import QXmlStreamReader

    reader = QXmlStreamReader(xml_bytes)
    root = None
    stack = []
    element_count = 0
    while not reader.atEnd():
        reader.readNext()
        if reader.isDTD() or reader.isEntityReference():
            raise ValueError('WMS-XML indeholder DTD eller entiteter, som ikke er tilladt.')
        if reader.isStartElement():
            element_count += 1
            if element_count > 200000 or len(stack) >= 64:
                raise ValueError('WMS-XML er for dyb eller har for mange elementer.')
            node = _XmlNode(
                _qt_string(reader.name()),
                {_qt_string(attr.name()): _qt_string(attr.value()) for attr in reader.attributes()},
            )
            if stack:
                stack[-1].children.append(node)
            elif root is None:
                root = node
            else:
                raise ValueError('WMS-XML indeholder flere rod-elementer.')
            stack.append(node)
        elif reader.isCharacters() and stack:
            stack[-1].text += _qt_string(reader.text())
        elif reader.isEndElement() and stack:
            stack.pop()
    if reader.hasError() or root is None or stack:
        raise ValueError('WMS returnerede ikke gyldig XML: ' + _qt_string(reader.errorString()))
    return root


def _parse_safe_xml(xml_bytes):
    """Parse untrusted WMS responses without DTDs, entities, or file access."""
    if _safe_etree is None:
        return _parse_with_qt(xml_bytes)
    try:
        return _safe_etree.fromstring(
            xml_bytes, forbid_dtd=True, forbid_entities=True, forbid_external=True,
        )
    except (_safe_etree.ParseError, DefusedXmlException) as exc:
        raise ValueError('WMS returnerede ikke gyldig eller sikker XML: ' + str(exc)) from exc


def read_capabilities(xml_bytes):
    """Extract advertised named WMS layers and inherited TIME dimensions."""
    if len(xml_bytes) > 20 * 1024 * 1024:
        raise ValueError('WMS GetCapabilities er uventet stor (>20 MB).')
    root = _parse_safe_xml(xml_bytes)
    if root.tag.rsplit('}', 1)[-1] in ('ServiceExceptionReport', 'ExceptionReport'):
        raise ValueError('WMS meldte en fejl: ' + ' '.join(root.itertext())[:300])
    caps = _children(root, 'Capability')
    if not caps:
        raise ValueError('XML mangler WMS Capability-elementet.')
    rows = []

    def visit(node, parents=(), inherited_time=False, depth=0):
        if depth > 64:
            raise ValueError('WMS GetCapabilities har for mange indlejrede lag.')
        title = _one_text(node, 'Title')
        name = _one_text(node, 'Name')
        has_time = inherited_time or any(
            dim.attrib.get('name', '').lower() == 'time'
            for dim in (_children(node, 'Dimension') + _children(node, 'Extent'))
        )
        if name:
            subtype = title or name
            theme = guess_wms_theme(subtype, parents)
            rows.append(WmsSource(theme, subtype, name, has_time,
                                  ' / '.join(parents + (subtype,))))
        for sub in _children(node, 'Layer'):
            visit(sub, parents + ((title,) if title else ()), has_time, depth + 1)

    for cap in caps:
        for layer in _children(cap, 'Layer'):
            visit(layer)
    if not rows:
        raise ValueError('Ingen navngivne WMS-lag fundet.')
    output = {}
    for item in rows:
        key = item.subtype
        current = output.setdefault(item.theme, {})
        if key in current:
            key = '{} ({})'.format(item.subtype, item.name)
        current[key] = [item]
    return output
