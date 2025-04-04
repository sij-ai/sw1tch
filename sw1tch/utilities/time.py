from datetime import datetime, timedelta
from typing import Tuple

from sw1tch import config

def get_current_utc() -> datetime:
    return datetime.utcnow()

def get_next_reset_time(now: datetime) -> datetime:
    reset_h = config["registration"]["token_reset_time_utc"] // 100
    reset_m = config["registration"]["token_reset_time_utc"] % 100
    candidate = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate

def get_downtime_start(next_reset: datetime) -> datetime:
    return next_reset - timedelta(minutes=config["registration"]["downtime_before_token_reset"])

def format_timedelta(td: timedelta) -> str:
    total_minutes = int(td.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    parts = []
    if hours == 1:
        parts.append("1 hour")
    elif hours > 1:
        parts.append(f"{hours} hours")
    if minutes == 1:
        parts.append("1 minute")
    elif minutes > 1:
        parts.append(f"{minutes} minutes")
    return " and ".join(parts) if parts else "0 minutes"

def get_time_until_reset_str(now: datetime) -> str:
    nr = get_next_reset_time(now)
    delta = nr - now
    return format_timedelta(delta)

def is_registration_closed(now: datetime) -> Tuple[bool, str]:
    nr = get_next_reset_time(now)
    ds = get_downtime_start(nr)
    if ds <= now < nr:
        time_until_open = nr - now
        msg = f"Registration is closed. It reopens in {format_timedelta(time_until_open)} at {nr.strftime('%H:%M UTC')}."
        return True, msg
    else:
        if now > ds:
            nr += timedelta(days=1)
            ds = get_downtime_start(nr)
        time_until_close = ds - now
        msg = f"Registration is open. It will close in {format_timedelta(time_until_close)} at {ds.strftime('%H:%M UTC')}."
        return False, msg
