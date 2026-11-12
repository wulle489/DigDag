"""Generate GDAL WMS XML descriptions without GetCapabilities requests.

QGIS 4.2/Qt6 has a documented regression when processing some gzip-compressed
GetCapabilities responses. GDAL's WMS minidriver can fetch GetMap images
without asking the QGIS WMS provider to parse GetCapabilities.

Descriptions live in the QGIS profile (instead of /tmp), so layers remain
available when the user saves and reopens a project.
"""

from hashlib import sha256
import os
from pathlib import Path
from urllib.parse import quote, urlsplit
from html import escape

from .wms import wms_endpoint_for_date


# The Danish territory in EPSG:25832, including Bornholm and southern Jutland,
# with ample margins. GDAL WMS uses virtual pixels and retrieves actual images
# in small blocks on demand -- this does not download an 8-million-pixel raster.
DATA_WINDOW = (250000, 6600000, 1050000, 5900000)
VIRTUAL_SIZE = (8000000, 7000000)


def _element(tag, value):
    """Serialize one fixed-name XML element, safely escaping its text."""
    return '<{0}>{1}</{0}>'.format(tag, escape(str(value), quote=True))


def xml_description(endpoint, layer_name, day):
    """Make a GDAL_WMS service description; does not require network access."""
    url = wms_endpoint_for_date(endpoint, day)
    parts = urlsplit(url)
    if parts.scheme.lower() != 'https' or not parts.netloc:
        raise ValueError('WMS kræver en gyldig HTTPS-adresse.')

    # All tag names are fixed constants. Variable text is escaped before
    # insertion, including token, layer name and parameters in ServerUrl.
    service = ''.join((
        '<Service name="WMS">',
        _element('Version', '1.3.0'),
        _element('ServerUrl', url + ('&' if '?' in url else '?')),
        _element('Layers', quote(layer_name, safe=':,')),
        _element('Styles', ''),
        _element('CRS', 'EPSG:25832'),
        _element('ImageFormat', 'image/png'),
        _element('Transparent', 'TRUE'),
        '</Service>',
    ))
    window = '<DataWindow>' + ''.join(
        _element(tag, value)
        for tag, value in zip(
            ('UpperLeftX', 'UpperLeftY', 'LowerRightX', 'LowerRightY'),
            DATA_WINDOW,
        )
    ) + ''.join((
        _element('SizeX', VIRTUAL_SIZE[0]),
        _element('SizeY', VIRTUAL_SIZE[1]),
        '</DataWindow>',
    ))
    body = ''.join((
        service, window,
        _element('Projection', 'EPSG:25832'),
        _element('BandsCount', 4),
        _element('BlockSizeX', 512),
        _element('BlockSizeY', 512),
        _element('MaxConnections', 2),
        _element('Timeout', 20),
    ))
    # GDAL requires <GDAL_WMS> to be the first bytes: no XML declaration.
    return ('<GDAL_WMS>' + body + '</GDAL_WMS>').encode('utf-8')


def persist_description(profile_directory, endpoint, layer_name, day):
    """Write immutable date-specific XML; keep it for persisted QGIS projects."""
    content = xml_description(endpoint, layer_name, day)
    basename = sha256(content).hexdigest()[:32] + '.xml'
    root = Path(profile_directory) / 'digdag_wms'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = root / basename
    if not dest.exists() or dest.read_bytes() != content:
        tmp = root / (basename + '.new')
        tmp.write_bytes(content)
        os.replace(str(tmp), str(dest))
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
    return str(dest)
