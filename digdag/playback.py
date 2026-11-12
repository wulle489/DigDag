"""Pure Python policies for DIGDAG timeline playback."""

from .temporal import MIN_YEAR, MAX_YEAR

PLAYBACK_STEP = 10
# A smaller denominator means a closer zoom. Playback is allowed for 1:1
# through 1:100 000, inclusive, and must NEVER change the user's scale.
MIN_PLAY_SCALE = 1.0
MAX_PLAY_SCALE = 100000.0
PLAYBACK_INTERVAL_MS = 1600
RENDER_POLL_MS = 200
RENDER_TIMEOUT_MS = 20000


def next_year(year, step=PLAYBACK_STEP):
    """Advance a decade, including 2010 for non-round starting years."""
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError('Årstal uden for DIGDAG-perioden.')
    if year >= MAX_YEAR:
        return None
    return min(year + step, MAX_YEAR)


def playback_scale_allowed(denominator):
    """Allow scale denominators [1, 100000], with a rounding tolerance."""
    return MIN_PLAY_SCALE <= float(denominator) <= MAX_PLAY_SCALE + 0.0001


def scale_too_detailed(denominator):
    """Retained as a compatibility helper; 1:100000 is NOT too detailed."""
    return float(denominator) < MIN_PLAY_SCALE
