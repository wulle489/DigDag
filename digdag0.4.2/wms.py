"""DIGDAG WMS URL and QGIS provider URI construction.

Pure Python helpers allow us to test URL/URI configuration outside QGIS.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def add_query_params(url, **params):
    """Replace query parameters case-insensitively and preserve token."""
    parts = urlsplit(url)
    new_keys = {name.casefold() for name in params}
    existing = [(key, value) for key, value
                in parse_qsl(parts.query, keep_blank_values=True)
                if key.casefold() not in new_keys]
    existing.extend((key, str(value)) for key, value in params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(existing), parts.fragment))


def wms_endpoint_for_date(base_url, date, parameter='time'):
    """Always send complete ISO date and avoid duplicate/stale time filters."""
    if parameter is None:
        parts = urlsplit(base_url)
        existing = [(key, value) for key, value
                    in parse_qsl(parts.query, keep_blank_values=True)
                    if key.casefold() != 'time']
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(existing), parts.fragment))
    return add_query_params(base_url, **{parameter: date.isoformat()})


def as_uri(url, layer_name, uri_factory):
    """Build a QGIS WMS URI that retains time on the actual GetMap URL.

    QGIS normally takes GetMap URL advertised by GetCapabilities and can lose
    custom query parameters present only on the user-specified base URL.
    IgnoreGetMapUrl forces it to use our URL with &time=YYYY-MM-DD.
    """
    uri = uri_factory()
    uri.setParam('url', url)
    uri.setParam('layers', layer_name)
    uri.setParam('styles', '')
    uri.setParam('format', 'image/png')
    uri.setParam('crs', 'EPSG:25832')
    uri.setParam('version', '1.3.0')
    uri.setParam('dpiMode', '7')
    uri.setParam('IgnoreGetMapUrl', '1')
    uri.setParam('IgnoreGetFeatureInfoUrl', '1')
    return bytes(uri.encodedUri()).decode('utf-8')


DATAFORSYNINGEN_URL = 'https://api.dataforsyningen.dk/digdag'


def endpoint_from_token(token):
    """The UI accepts only the token, not a WMS endpoint."""
    token = str(token or '').strip()
    return add_query_params(DATAFORSYNINGEN_URL, token=token) if token else DATAFORSYNINGEN_URL


def token_from_legacy_url(url):
    """Migrate the old v0.2 full-URL setting into the token-only UI."""
    return next((value for key, value in parse_qsl(urlsplit(str(url or '')).query,
                                                    keep_blank_values=True)
                 if key.casefold() == 'token'), '')
