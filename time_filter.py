"""Local calendar ranges with exclusive timestamp upper bounds."""
import datetime as dt
import re


def parse_time_range(start_text, end_text):
    def boundary(text, is_end):
        text = text.strip()
        if not text:
            return None
        if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}(?:-\d{1,2})?', text):
            raise ValueError('Invalid date or hour')
        parts = [int(part) for part in text.split('-')]
        value = dt.datetime(*parts)
        if is_end and len(parts) == 3:
            value += dt.timedelta(days=1)
        return value.timestamp()

    try:
        start, end = boundary(start_text, False), boundary(end_text, True)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('请输入有效日期或整点，例如 2026-11-09 或 2026-11-09-1；小时为 0–23。') from exc
    if start is not None and end is not None and start >= end:
        raise ValueError('结束时间必须晚于开始时间；仅填结束日期时包含当天。')
    return start, end


def quick_range(preset, today=None):
    today = today or dt.date.today()
    if preset == 'all':
        return '', ''
    if preset == 'yesterday':
        day = (today - dt.timedelta(days=1)).isoformat()
        return day, day
    if preset == 'week':
        return (today - dt.timedelta(days=7)).isoformat(), today.isoformat()
    if preset == 'today':
        return today.isoformat(), today.isoformat()
    raise ValueError('未知时间范围')
