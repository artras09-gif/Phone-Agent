"""Строго вчера / позавчера, без подмены дня и резервной суммы."""
import datetime as dt
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import accstats as a

with tempfile.TemporaryDirectory() as tmp:
    for net, name in a.SHOT_FILES.items():
        target = dt.date.today() - dt.timedelta(days=2 if net == "tiktok" else 1)
        for when, valid in [(f"{target:%d.%m}", True),
                            (f"{target - dt.timedelta(days=1):%d.%m}", False),
                            ("последние 7 дней", False), ("", False)]:
            a._views_note(tmp, name, "0", when)
            path = Path(tmp, name + ".png")
            path.write_bytes(b"test")
            result = {"shot": str(path), "why": ""}
            a._require_daily_date(tmp, net, result)
            assert bool(result["shot"]) == valid
            assert path.exists() == valid
            notes = a._notes_in(tmp)
            assert bool(notes[name + "_views"]) == valid
            if valid:
                assert notes[name + "_end"] == target.isoformat()
            else:
                assert f"{target:%d.%m}" in notes[name + "_why"]
    target = dt.date.today() - dt.timedelta(days=2)
    picked = []
    a._tt_blind_calendar = lambda *x: True
    a._tt_blind_pick = lambda first, last, *x: picked.append((first, last)) or True
    a._tt_blind_label = lambda: f"{picked[-1][0]:%b %d} - {picked[-1][1]:%b %d}"
    assert a._tt_period(1, 1280, 2772, blind=True)
    assert picked[-1] == (target, target)
    a._tt_blind_label = lambda: "Last 7 days"
    assert a._tt_period(1, 1280, 2772, blind=True) is None
print("PASS: четыре сети, точные даты, ноль, отказ от другого периода, календарь TikTok")
