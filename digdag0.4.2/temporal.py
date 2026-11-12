"""Date parsing and OGR attribute-filter construction. No QGIS dependency."""

import calendar
from datetime import date
import re

MIN_YEAR = 1660
MAX_YEAR = 2010


def parse_date(value):
    value = str(value).strip()
    # Also accept full Danish DD.MM.YYYY.
    match = re.fullmatch(r'(\d{1,2})[.](\d{1,2})[.](\d{4})', value)
    if match:
        day, month, year = map(int, match.groups())
        precision = 'day'
    else:
        match = re.fullmatch(r'(\d{4})(?:[-/.](\d{1,2}))?(?:[-/.](\d{1,2}))?', value)
        if not match:
            raise ValueError('Brug 1900, 1900-06 eller 1900-06-15.')
        year = int(match.group(1))
        month = int(match.group(2) or 1)
        day = int(match.group(3) or 1)
        precision = 'day' if match.group(3) else 'month' if match.group(2) else 'year'
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise ValueError('Årstallet skal være mellem 1660 og 2010.')
    try:
        return date(year, month, day), precision
    except ValueError as exc:
        raise ValueError('Ugyldig dato: ' + value) from exc


def with_year(day, year):
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def choose_fields(names):
    """Return FRA / TIL equivalents; reject unmatched fields explicitly."""
    lookup = {re.sub(r'[^a-z0-9]', '', str(name).lower()): str(name)
              for name in names}
    starts = ('fra', 'gyldigfra', 'fradato', 'startdato', 'start', 'startdate', 'validfrom')
    ends = ('til', 'gyldigtil', 'tildato', 'slutdato', 'slut', 'enddate', 'validto')
    def first(options):
        for option in options:
            if option in lookup:
                return lookup[option]
        return None
    return first(starts), first(ends)


def ogr_filter(day, start_name, end_name, start_numeric=False, end_numeric=False):
    """Build a conservative OGR SetAttributeFilter-compatible subset SQL.

    Empty date intervals are treated as unbounded. Strings must contain ISO
    YYYY-MM-DD (or comparable complete date values); numeric fields are years.
    """
    if not start_name or not end_name:
        raise ValueError('Der skal være både et fra- og et til-felt.')
    clauses = []
    for name, direction, numeric in ((start_name, '<=', start_numeric),
                                     (end_name, '>=', end_numeric)):
        safe = '"' + name.replace('"', '""') + '"'
        value = str(day.year) if numeric else "'{}'".format(day.isoformat())
        clauses.append('({0} IS NULL OR {0} {1} {2})'.format(safe, direction, value))
    return ' AND '.join(clauses)
