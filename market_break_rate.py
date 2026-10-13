"""Collect Eastmoney limit-up/broken-limit pools through AKShare."""
import argparse
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from market_amount import DEFAULT_DB, target_date

SCHEMA = '''CREATE TABLE IF NOT EXISTS market_break_rate (
    date TEXT PRIMARY KEY,
    zt_count INTEGER NOT NULL,
    zb_count INTEGER NOT NULL,
    break_rate REAL NOT NULL
)'''


def update(ak, db_path, day):
    api_date = day.strftime('%Y%m%d')
    zt = ak.stock_zt_pool_em(date=api_date)
    zb = ak.stock_zt_pool_zbgc_em(date=api_date)
    # AKShare returns the same empty frame for missing data and an empty pool.
    # Do not turn an unavailable response into an apparently valid 0%/100%.
    if zt.empty or zb.empty:
        print(f'{day}: 股池为空（可能休市、零股票或数据不可用），未写入，请核实', flush=True)
        return False
    for frame in (zt, zb):
        if '代码' not in frame.columns or frame['代码'].isna().any() or frame['代码'].duplicated().any():
            raise ValueError('股池股票代码缺失或重复，未写入')
    if set(zt['代码']) & set(zb['代码']):
        raise ValueError('涨停池与炸板池股票重叠，未写入')
    zt_count, zb_count = len(zt), len(zb)
    rate = zb_count / (zt_count + zb_count)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        with conn:
            conn.execute(SCHEMA)
            conn.execute('''INSERT INTO market_break_rate VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET zt_count=excluded.zt_count,
                zb_count=excluded.zb_count, break_rate=excluded.break_rate''',
                (day.isoformat(), zt_count, zb_count, rate))
    print(f'{day}: 涨停 {zt_count}，炸板 {zb_count}，炸板率 {rate:.2%}', flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(DEFAULT_DB))
    parser.add_argument(
        '--date',
        type=date.fromisoformat,
        help='手动指定日期，例如 2026-09-08'
    )
    args = parser.parse_args()

    import akshare as ak

    # 手动指定日期时，只处理这一天
    if args.date:
        update(ak, args.db, args.date)
        return

    # 自动运行时，检查最近 7 天
    end_day = target_date()

    for days_ago in range(6, -1, -1):
        day = end_day - timedelta(days=days_ago)

        # 周末跳过
        if day.weekday() >= 5:
            continue

        # 检查数据库中是否已经存在
        with closing(sqlite3.connect(args.db, timeout=30)) as conn:
            conn.execute(SCHEMA)
            exists = conn.execute(
                'SELECT 1 FROM market_break_rate WHERE date = ?',
                (day.isoformat(),)
            ).fetchone()

        if exists:
            print(f'{day}: 已存在，跳过', flush=True)
            continue

        try:
            update(ak, args.db, day)
        except Exception as exc:
            print(f'{day}: 获取失败：{exc}', flush=True)


if __name__ == '__main__':
    main()
