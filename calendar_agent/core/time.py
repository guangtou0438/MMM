from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from ..config import DISPLAY_TIMEZONE


def resolve_local(value: str) -> datetime:
    """Reject both DST gaps and ambiguous folds instead of choosing silently."""
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        raise ValueError("本地输入请使用不带偏移的 Melbourne 墙上时间。")
    zone = ZoneInfo(DISPLAY_TIMEZONE)
    possibilities = set()
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == naive:
            possibilities.add(candidate)
    if not possibilities:
        raise ValueError(f"{value} 在 Melbourne 夏令时切换中不存在，请选择其他时间。")
    if len(possibilities) > 1:
        raise ValueError(f"{value} 在 Melbourne 夏令时切换中出现两次，请选择无歧义的时间，或通过 API 提交明确 UTC 偏移。")
    return possibilities.pop()
