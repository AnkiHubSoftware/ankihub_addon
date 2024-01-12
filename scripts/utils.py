def to_point_version(anki_version_str: str) -> int:
    """Converts a full version (e.g. 2.1.66) to a point version (e.g. 230900).
    In versions before 23.10, the point version is just the last number."""
    if anki_version_str.startswith("2.1."):
        # for Anki versions before 23.10
        last_part = anki_version_str.split(".")[-1]
        return int(last_part)
    else:
        # for Anki versions 23.10 and later
        # adapted from anki.utils.point_version
        try:
            [year, month, patch] = anki_version_str.split(".")
        except ValueError:
            [year, month] = anki_version_str.split(".")
            patch = "0"

        year_num = int(year)
        month_num = int(month)
        patch_num = int(patch)
        return year_num * 10_000 + month_num * 100 + patch_num


def to_version_str(point_version: int) -> str:
    """Converts a point version (e.g. 231000) to a full version (e.g. 23.10).
    Versions before 23.10 are converted to 2.1.x, e.g. 66 becomes 2.1.66."""
    if point_version < 23_10_0:
        # for Anki versions before 23.10
        return f"2.1.{point_version}"
    else:
        # for Anki versions 23.10 and later
        year = point_version // 10_000
        month = (point_version - year * 10_000) // 100
        patch = point_version - year * 10_000 - month * 100
        return f"{year}.{month}.{patch}"
