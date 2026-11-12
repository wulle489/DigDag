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
import xml.etree.ElementTree as ET

from .wms import wms_endpoint_for_date


# The Danish territory in EPSG:25832, including Bornholm and southern Jutland,
# with ample margins. GDAL WMS uses virtual pixels and retrieves actual images
# in small blocks on demand -- this does not download an 8-million-pixel raster.
DATA_WINDOW = (250000, 6600000, 1050000, 5900000)
VIRTUAL_SIZE = (8000000, 7000000)


def xml_description(endpoint, layer_name, day):
    """Make a GDAL_WMS service description; does not require network access."""
    url = wms_endpoint_for_date(endpoint, day)
    parts = urlsplit(url)
    if parts.scheme.lower() != 'https' or not parts.netloc:
        raise ValueError('WMS kræver en gyldig HTTPS-adresse.')

    root = ET.Element('GDAL_WMS')
    svc = ET.SubElement(root, 'Service', {'name': 'WMS'})
    ET.SubElement(svc, 'Version').text = '1.3.0'
    # GDAL appends SERVICE, REQUEST, WIDTH, HEIGHT and BBOX to this URL.
    ET.SubElement(svc, 'ServerUrl').text = url + ('&' if '?' in url else '?')
    ET.SubElement(svc, 'Layers').text = quote(layer_name, safe=':,')
    ET.SubElement(svc, 'Styles').text = ''
    ET.SubElement(svc, 'CRS').text = 'EPSG:25832'
    ET.SubElement(svc, 'ImageFormat').text = 'image/png'
    ET.SubElement(svc, 'Transparent').text = 'TRUE'
    dw = ET.SubElement(root, 'DataWindow')
    for tag, value in zip(('UpperLeftX', 'UpperLeftY', 'LowerRightX', 'LowerRightY'), DATA_WINDOW):
        ET.SubElement(dw, tag).text = str(value)
    ET.SubElement(dw, 'SizeX').text = str(VIRTUAL_SIZE[0])
    ET.SubElement(dw, 'SizeY').text = str(VIRTUAL_SIZE[1])
    ET.SubElement(root, 'Projection').text = 'EPSG:25832'
    ET.SubElement(root, 'BandsCount').text = '4'
    ET.SubElement(root, 'BlockSizeX').text = '512'
    ET.SubElement(root, 'BlockSizeY').text = '512'
    ET.SubElement(root, 'MaxConnections').text = '2'
    ET.SubElement(root, 'Timeout').text = '20'
    # GDAL WMS recognition requires <GDAL_WMS> as the FIRST bytes: no XML declaration.
    return ET.tostring(root, encoding='utf-8', xml_declaration=False)


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
