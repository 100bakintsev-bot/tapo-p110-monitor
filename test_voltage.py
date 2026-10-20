import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import voltage_data as v


def report(value, reachable=True):
    return {"reachable": reachable, "measurements": {"voltage": {
        "value": value, "unit": "V", "status": "available" if value is not None else "not_reported"}}}


class VoltageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "v.sqlite3"
        self.db = v.connect_writer(self.path)
        self.t = datetime(2026, 10, 20, 10, tzinfo=v.TZ).timestamp()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_aggregate_dedup_failure_and_zero(self):
        for offset, value in ((0, 220), (10, 240), (11, 999), (20, None), (30, 0)):
            v.record(self.db, report(value), self.t + offset)
        row = self.db.execute("SELECT n,total,low,high,attempts FROM voltage_minutes").fetchone()
        self.assertEqual(row, (3, 460, 0, 240, 4))
        text = v.day_text(path=self.path, now=self.t+40)
        self.assertIn("153,3 В", text)
        self.assertIn("75%", text)
        self.assertIn("До початку збору", text)

    def test_stale_offline_no_false_voltage(self):
        v.record(self.db, report(227), self.t)
        self.assertIn("227,0 В", v.status_text(self.path, self.t+1))
        self.assertNotIn("227,0", v.status_text(self.path, self.t+100))
        v.record(self.db, report(None, False), self.t+110)
        text = v.status_text(self.path, self.t+111)
        self.assertIn("недоступне", text)
        self.assertNotIn("0,0 В", text)
        self.assertIn("227,0 В", v.day_text(path=self.path, now=self.t+120))

    def test_midnight_and_missing_day(self):
        midnight = datetime(2026, 10, 21, tzinfo=v.TZ).timestamp()
        v.record(self.db, report(220), midnight-10)
        v.record(self.db, report(240), midnight+10)
        today = v.day_text(path=self.path, now=midnight+20)
        yesterday = v.day_text(datetime(2026,10,20).date(), self.path, midnight+20)
        self.assertIn("240,0", today); self.assertNotIn("220,0", today)
        self.assertIn("220,0", yesterday); self.assertNotIn("240,0", yesterday)
        self.assertIn("немає", v.day_text(datetime(2026,10,19).date(), self.path, midnight+20))

    def test_hourly_weighted_and_restart(self):
        v.record(self.db, report(220), self.t)
        self.db.close(); self.db = v.connect_writer(self.path)
        v.record(self.db, report(240), self.t+3600)
        text = v.day_text(path=self.path, now=self.t+3610, hourly=True)
        self.assertIn("230,0", text)
        self.assertIn("10:00", text); self.assertIn("11:00", text)
        self.assertLess(len(text), 4096)

    def test_invalid_and_missing_database(self):
        for i, value in enumerate((True, float("nan"), -1, None)):
            v.record(self.db, report(value), self.t + i*10)
        self.assertEqual(self.db.execute("SELECT SUM(n) FROM voltage_minutes").fetchone()[0],0)
        self.assertIn("недоступна", v.status_text(Path(self.temp.name)/"absent"))

    def test_bot_routing_and_existing_grid_source(self):
        from examples import bot_integration as bot
        with patch.object(v,"status_text",return_value="\n227,0 В"):
            text = bot.status_message("🔦 Світла немає")
            self.assertIn("Світла немає",text)
            self.assertIn("227,0 В",text)
        with patch.object(v,"day_text",return_value="test summary"):
            self.assertEqual(bot.voltage_today_message(),"test summary")
            self.assertEqual(bot.append_voltage_to_daily_report("existing",None),"existing\n\ntest summary")
        self.assertEqual(bot.menu_button(),{"text":"⚡ Напруга за день"})

    def test_complete_day_text_and_clock_change(self):
        start = datetime(2026,10,25,tzinfo=v.TZ).timestamp()
        end = datetime(2026,10,26,tzinfo=v.TZ).timestamp()
        self.assertEqual(end-start,25*3600)
        for stamp in range(int(start),int(end),10):
            v.record(self.db,report(227),stamp)
        text = v.day_text(datetime(2026,10,25).date(),self.path,end,hourly=True)
        self.assertIn("100%",text)
        self.assertLess(len(text),4096)
        self.assertIn("03:00 +0300",text)
        self.assertIn("03:00 +0200",text)


if __name__ == "__main__":
    unittest.main()
