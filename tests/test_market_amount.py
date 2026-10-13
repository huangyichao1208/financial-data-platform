import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
import pandas as pd
from zoneinfo import ZoneInfo

from market_amount import update, target_date


class AKShare:
    def __init__(self, sh, sz):
        self.data = {'sh000001': sh, 'sz399001': sz}
        self.calls = []

    def stock_zh_index_daily_em(self, **kwargs):
        self.calls.append(kwargs)
        return pd.DataFrame(self.data[kwargs['symbol']], columns=['date', 'amount'])


class MarketTests(unittest.TestCase):
    def test_updates_and_missing_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            day = date(2026, 9, 4)
            bs = AKShare([['2026-09-04', '100000000']], [['2026-09-04', '200000000']])
            self.assertEqual(update(bs, path, day, day), 1)
            self.assertEqual(bs.calls[0], {'symbol': 'sh000001', 'start_date': '20260904', 'end_date': '20260904'})
            update(bs, path, day, day)
            self.assertEqual(update(AKShare([], []), path, day, day), 0)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT total_amount FROM market_amount').fetchall(), [(300000000,)])
            with self.assertRaises(RuntimeError):
                update(AKShare([['2026-09-04', '1']], []), path, day, day)

    def test_invalid_amount(self):
        bs = AKShare([['2026-09-04', 'nan']], [])
        with self.assertRaises(RuntimeError):
            update(bs, ':memory:', date(2026, 9, 4), date(2026, 9, 4))

    def test_range_and_duplicate_dates(self):
        from market_amount import fetch_amounts
        day = date(2026, 9, 4)
        ak = AKShare([['2026-09-03', '99'], [day, '100']], [])
        self.assertEqual(fetch_amounts(ak, 'sh000001', day, day), {'2026-09-04': 100})
        ak = AKShare([[day, '100'], [day, '200']], [])
        with self.assertRaises(RuntimeError):
            fetch_amounts(ak, 'sh000001', day, day)

    def test_provider_failure_preserves_existing_values(self):
        from unittest.mock import patch
        day = date(2026, 9, 4)
        ak = AKShare([[day, '100']], [[day, '200']])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.db'
            update(ak, path, day, day)
            with patch.object(ak, 'stock_zh_index_daily_em', side_effect=RuntimeError('network')):
                with self.assertRaises(RuntimeError):
                    update(ak, path, day, day)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT total_amount FROM market_amount').fetchone(), (300,))

    def test_vancouver_morning_targets_china_previous_day(self):
        for month in (1, 7):
            now = datetime(2026, month, 15, 10, tzinfo=ZoneInfo('America/Vancouver'))
            self.assertEqual(target_date(now), date(2026, month, 15))

    def test_china_midnight_and_month_boundary(self):
        now = datetime(2026, 9, 8, 10, tzinfo=ZoneInfo('America/Vancouver'))
        china = now.astimezone(ZoneInfo('Asia/Shanghai'))
        self.assertEqual(china.isoformat(), '2026-09-09T01:00:00+08:00')
        self.assertEqual(target_date(now), date(2026, 9, 8))
        self.assertEqual(target_date(datetime(2026, 10, 1, 1,
            tzinfo=ZoneInfo('Asia/Shanghai'))), date(2026, 9, 30))

    def test_pages(self):
        import app as module
        with tempfile.TemporaryDirectory() as tmp:
            old_path, old_ready = module.DB_PATH, module._db_ready
            try:
                module.DB_PATH = Path(tmp) / 'web.sqlite3'
                module.init_db()
                module._db_ready = True
                client = module.app.test_client()
                for url in ('/sentiment', '/sentiment/amount', '/sentiment/break-rate'):
                    self.assertEqual(client.get(url).status_code, 200)
                with sqlite3.connect(module.DB_PATH) as conn:
                    conn.execute("INSERT INTO market_amount VALUES ('2026-09-04', 100000000, 200000000, 300000000)")
                html = client.get('/sentiment/amount').get_data(as_text=True)
                self.assertIn('2026-09-04', html)
                self.assertIn('3.00', html)
            finally:
                module.DB_PATH, module._db_ready = old_path, old_ready


if __name__ == '__main__':
    unittest.main()
