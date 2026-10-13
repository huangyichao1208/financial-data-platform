"""Fetch SSE/SZSE daily stock turnover through AKShare; store amounts in yuan."""
import argparse
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """CREATE TABLE IF NOT EXISTS market_amount (
    date TEXT PRIMARY KEY,
    sh_amount REAL NOT NULL,
    sz_amount REAL NOT NULL,
    total_amount REAL NOT NULL
)"""
DEFAULT_DB = Path(__file__).resolve().parent / 'finance_guide.sqlite3'


def target_date(now=None):
    """China yesterday: at Vancouver 10:00, fetch the prior China trading date.

    Daily bars use a date, not a 15:00 timestamp; holidays have no row.
    """
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    return now.astimezone(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=1)


def extract_amount(frame, label_column, label, value_column, multiplier):
    if frame.empty:
        return None
    if not {label_column, value_column}.issubset(frame.columns):
        raise RuntimeError(f'交易所返回字段缺失: {label_column}/{value_column}')
    values = frame.loc[frame[label_column] == label, value_column]
    if len(values) != 1:
        raise RuntimeError(f'交易所返回 {label} 行数异常: {len(values)}')
    amount = float(str(values.iloc[0]).replace(',', '').strip()) * multiplier
    if not math.isfinite(amount) or amount < 0:
        raise RuntimeError(f'交易所成交金额无效: {values.iloc[0]}')
    return amount


def update(ak, db_path, start, end):
    records = []
    day = start
    while day <= end:
        # Chinese stock exchanges do not trade on weekends.
        if day.weekday() < 5:
            api_date = day.strftime('%Y%m%d')
            sh_frame = ak.stock_sse_deal_daily(date=api_date)
            sz_frame = ak.stock_szse_summary(date=api_date)
            # Use the stock totals (including B shares), not funds/bonds or
            # the sum of totals and their component categories.
            sh = extract_amount(sh_frame, '单日情况', '成交金额', '股票', 1e8)
            sz = extract_amount(sz_frame, '证券类别', '股票', '成交金额', 1)
            if sh in (None, 0) and sz in (None, 0):
                print(f'{day}: 无成交数据，跳过', flush=True)
            elif sh in (None, 0) or sz in (None, 0):
                raise RuntimeError(f'{day}: 沪深成交数据不完整，未写入；请稍后重试')
            else:
                records.append((day.isoformat(), sh, sz, sh + sz))
        day += timedelta(days=1)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        with conn:
            conn.execute(SCHEMA)
            conn.executemany('''INSERT INTO market_amount VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET sh_amount=excluded.sh_amount,
                sz_amount=excluded.sz_amount, total_amount=excluded.total_amount''', records)
    for day, sh, sz, total in records:
        print(f'{day}: 沪市 {sh / 1e8:.2f} 亿元，深市 {sz / 1e8:.2f} 亿元，'
              f'合计 {total / 1e8:.2f} 亿元', flush=True)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--start-date', type=date.fromisoformat)
    parser.add_argument('--end-date', type=date.fromisoformat, default=target_date())
    args = parser.parse_args()
    # Refresh a week of overlap; recover longer gaps since the last stored date.
    start = args.start_date
    if start is None:
        start = args.end_date - timedelta(days=7)
        if args.db.exists():
            with closing(sqlite3.connect(args.db)) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='market_amount'").fetchone():
                    latest = conn.execute('SELECT MAX(date) FROM market_amount').fetchone()[0]
                    if latest:
                        start = min(start, date.fromisoformat(latest))
    if start > args.end_date:
        parser.error('start-date must not be after end-date')
    import akshare as ak
    count = update(ak, args.db, start, args.end_date)
    print(f'{start} ~ {args.end_date}: 更新 {count} 个交易日' if count else '无成交数据，跳过')


if __name__ == '__main__':
    main()
