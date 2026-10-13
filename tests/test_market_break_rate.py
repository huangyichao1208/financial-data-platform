import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from market_break_rate import update
import collect_sentiment


class BreakRateTests(unittest.TestCase):
    def test_counts_date_and_upsert(self):
        ak = Mock()
        ak.stock_zt_pool_em.return_value = pd.DataFrame({'代码': ['a', 'b', 'c'], '序号': [9, 5, 1]})
        ak.stock_zt_pool_zbgc_em.return_value = pd.DataFrame({'代码': ['d']})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.db'
            for _ in range(2):
                self.assertTrue(update(ak, path, date(2026, 9, 7)))
            ak.stock_zt_pool_em.assert_called_with(date='20260907')
            ak.stock_zt_pool_zbgc_em.assert_called_with(date='20260907')
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT * FROM market_break_rate').fetchall(),
                                 [('2026-09-07', 3, 1, .25)])
            ak.stock_zt_pool_zbgc_em.return_value = pd.DataFrame()
            self.assertFalse(update(ak, path, date(2026, 9, 7)))
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT break_rate FROM market_break_rate').fetchone(), (.25,))

    def test_exception_does_not_create_database(self):
        ak = Mock()
        ak.stock_zt_pool_em.side_effect = RuntimeError('network')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.db'
            with self.assertRaises(RuntimeError):
                update(ak, path, date(2026, 9, 7))
            self.assertFalse(path.exists())

    def test_collectors_are_independent(self):
        with patch('collect_sentiment.subprocess.run', side_effect=[
            collect_sentiment.subprocess.TimeoutExpired('amount', 240), Mock(returncode=0)
        ]) as run:
            self.assertEqual(collect_sentiment.main(), 1)
            self.assertEqual(run.call_count, 2)

    def test_table(self):
        import app as module
        with tempfile.TemporaryDirectory() as tmp:
            old_path, old_ready = module.DB_PATH, module._db_ready
            try:
                module.DB_PATH = Path(tmp) / 'web.db'
                module.init_db()
                module._db_ready = True
                with sqlite3.connect(module.DB_PATH) as conn:
                    conn.execute("INSERT INTO market_break_rate VALUES ('2026-09-07', 3, 1, .25)")
                response = module.app.test_client().get('/sentiment/break-rate')
                self.assertEqual(response.status_code, 200)
                self.assertIn('25.00%', response.get_data(as_text=True))
            finally:
                module.DB_PATH, module._db_ready = old_path, old_ready
