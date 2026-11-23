BAND_TABLE_VERSION = "academic-reading-2026-1"

_BANDS = (
    (39, "9.0"), (37, "8.5"), (35, "8.0"), (33, "7.5"), (30, "7.0"), (27, "6.5"),
    (23, "6.0"), (19, "5.5"), (15, "5.0"), (13, "4.5"), (10, "4.0"),
)


def band_for(correct: int, total: int, test_type: str) -> str | None:
    if test_type != "ielts_standard" or total != 40:
        return None
    for minimum, band in _BANDS:
        if correct >= minimum:
            return band
    return "below_4.0"
