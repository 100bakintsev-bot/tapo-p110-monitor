"""Tapo voltage history, separate from the bot's electricity event database."""
import json
import math
import sqlite3
import time
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TAPO_TIMEZONE", "Europe/Kyiv"))
ROOT = Path(os.environ.get("TAPO_DATA_DIR", str(Path.home() / ".local/share/tapo-p110-monitor"))).expanduser()
DBFILE = ROOT / "voltage.sqlite3"
INTERVAL = 10
FRESH = 90


def connect_writer(path=DBFILE):
    db = sqlite3.connect(path, timeout=3)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS voltage_minutes (
            minute INTEGER PRIMARY KEY, n INTEGER NOT NULL, total REAL NOT NULL,
            low REAL, low_at REAL, high REAL, high_at REAL,
            attempts INTEGER NOT NULL, last_slot INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS voltage_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    db.commit()
    return db


def record(db, report, now=None):
    """Each ten-second slot counts once; failures are never inserted as zero volts."""
    now = time.time() if now is None else now
    minute, slot = int(now // 60) * 60, int(now // INTERVAL)
    item = report.get("measurements", {}).get("voltage", {})
    value = item.get("value")
    valid = (report.get("reachable") is True and item.get("status") == "available"
             and item.get("unit") == "V" and isinstance(value, (int, float))
             and not isinstance(value, bool) and math.isfinite(value) and value >= 0)
    v = value if valid else None
    with db:
        db.execute("INSERT OR IGNORE INTO voltage_meta VALUES('started_at',?)", (str(now),))
        old = db.execute("SELECT last_slot FROM voltage_minutes WHERE minute=?", (minute,)).fetchone()
        if old and slot <= old[0]:
            return
        db.execute("""
            INSERT INTO voltage_minutes VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(minute) DO UPDATE SET
              n=n+excluded.n, total=total+excluded.total,
              low_at=CASE WHEN excluded.low IS NOT NULL AND (low IS NULL OR excluded.low<low)
                          THEN excluded.low_at ELSE low_at END,
              low=CASE WHEN excluded.low IS NOT NULL AND (low IS NULL OR excluded.low<low)
                       THEN excluded.low ELSE low END,
              high_at=CASE WHEN excluded.high IS NOT NULL AND (high IS NULL OR excluded.high>high)
                           THEN excluded.high_at ELSE high_at END,
              high=CASE WHEN excluded.high IS NOT NULL AND (high IS NULL OR excluded.high>high)
                        THEN excluded.high ELSE high END,
              attempts=attempts+1, last_slot=excluded.last_slot
        """, (minute, int(valid), v or 0, v, now if valid else None, v,
              now if valid else None, 1, slot))
        db.execute("INSERT OR REPLACE INTO voltage_meta VALUES('latest',?)",
                   (json.dumps({"at": now, "value": v, "reachable": report.get("reachable") is True}),))
        # Minute aggregates: about 526k rows per year; bounded retention.
        db.execute("DELETE FROM voltage_minutes WHERE minute<?", (minute - 366 * 86400,))


@contextmanager
def reader(path=DBFILE):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.3)
    try:
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def decimal(value):
    return f"{value:.1f}".replace(".", ",")


def status_text(path=DBFILE, now=None):
    now = time.time() if now is None else now
    try:
        with reader(path) as db:
            row = db.execute("SELECT value FROM voltage_meta WHERE key='latest'").fetchone()
            if not row:
                return "\n\n⚡ Напруга: очікуємо перше вимірювання."
            latest = json.loads(row[0])
            age = now - latest["at"]
            if age < -5 or age > FRESH:
                return "\n\n⚡ Напруга: немає свіжих даних.\nСтан світла визначає датчик телефона."
            if latest["value"] is None:
                return "\n\n⚡ Напруга: вимірювання недоступне.\nЦе не є підтвердженням відключення світла."
            minute = int(latest["at"] // 60) * 60
            bounds = db.execute("SELECT low,high FROM voltage_minutes WHERE minute=?", (minute,)).fetchone()
            lines = ["", "", f"⚡ Напруга: {decimal(latest['value'])} В"]
            if bounds and bounds[0] is not None:
                lines.append(f"У хвилині останнього вимірювання: {decimal(bounds[0])}–{decimal(bounds[1])} В")
            lines.append(f"Виміряно: {datetime.fromtimestamp(latest['at'], TZ):%H:%M:%S} · {max(0, int(age))} с тому")
            return "\n".join(lines)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return "\n\n⚡ Напруга: історія тимчасово недоступна."


def day_text(day=None, path=DBFILE, now=None, hourly=False):
    now = time.time() if now is None else now
    day = day or datetime.fromtimestamp(now, TZ).date()
    midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
    start, end = midnight.timestamp(), (midnight + timedelta(days=1)).timestamp()
    end = min(end, now)
    title = f"⚡ Напруга за {day:%d.%m.%Y}"
    try:
        with reader(path) as db:
            rows = db.execute("SELECT minute,n,total,low,low_at,high,high_at FROM voltage_minutes "
                              "WHERE minute>=? AND minute<? ORDER BY minute", (start, end)).fetchall()
            begun = db.execute("SELECT value FROM voltage_meta WHERE key='started_at'").fetchone()
        rows_valid = [r for r in rows if r[1]]
        if not rows_valid:
            return title + "\nВимірювань за цей день немає. Пропуски не означають 0 В."
        count = sum(r[1] for r in rows_valid)
        total = sum(r[2] for r in rows_valid)
        low = min(rows_valid, key=lambda r: r[3])
        high = max(rows_valid, key=lambda r: r[5])
        observed_start = max(start, float(begun[0]) if begun else start)
        coverage = min(100, count * INTERVAL / max(INTERVAL, end - observed_start) * 100)
        lines = [title, f"Середня за вимірюваннями: {decimal(total/count)} В",
                 f"Найнижча: {decimal(low[3])} В о {datetime.fromtimestamp(low[4], TZ):%H:%M:%S}",
                 f"Найвища: {decimal(high[5])} В о {datetime.fromtimestamp(high[6], TZ):%H:%M:%S}",
                 f"Охоплення опитуваннями: приблизно {coverage:.0f}% часу спостереження.",
                 f"Період: {datetime.fromtimestamp(observed_start, TZ):%H:%M:%S}–{datetime.fromtimestamp(end, TZ):%H:%M:%S}."]
        if observed_start > start:
            lines.append("До початку збору історія відсутня.")
        if hourly:
            hours = {}
            for r in rows_valid:
                label = datetime.fromtimestamp(r[0], TZ).strftime("%H:00 %z")
                a = hours.setdefault(label, [0, 0, r[3], r[5]])
                a[0] += r[1]; a[1] += r[2]; a[2] = min(a[2], r[3]); a[3] = max(a[3], r[5])
            lines.append("\nЗа годинами: середня · мінімум–максимум")
            for label, (n, total, lo, hi) in hours.items():
                lines.append(f"{label} — {decimal(total/n)} · {decimal(lo)}–{decimal(hi)} В")
        lines.append("Короткі зміни між опитуваннями можуть бути пропущені.")
        return "\n".join(lines)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return title + "\nІсторія вимірювань тимчасово недоступна."
