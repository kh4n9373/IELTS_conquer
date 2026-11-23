"""The one stored format for a key source region: fractions of the displayed page, origin top-left."""

import math

BBox = tuple[float, float, float, float]


def fraction_box(x0: float, y0: float, x1: float, y1: float, width: float, height: float) -> BBox | None:
    """Returns (x0, y0, x1, y1) as 0 to 1, or None when the size or box cannot be trusted."""
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1, width, height)) or width <= 0 or height <= 0:
        return None
    box = (x0 / width, y0 / height, x1 / width, y1 / height)
    if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        return None
    return box
