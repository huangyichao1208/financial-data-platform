import json
from market_break_rate import SCHEMA as MARKET_BREAK_RATE_SCHEMA
from market_amount import SCHEMA as MARKET_AMOUNT_SCHEMA
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "finance_guide.sqlite3"
GAPSTOCK_DIR = BASE_DIR / "data" / "gapstocks"
ACTIVE_PERIOD_PATH = BASE_DIR / "data" / "active_period.json"
CODE_NAME_PATH = BASE_DIR / "data" / "code_name.json"
QUARTERS = ("0331", "0630", "0930", "1231")
QUARTER_LABELS = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}
CUSTOM_STOCK_SOURCES = {
    "info_gap": "信息差类公司",
    "resources": "资源类公司",
    "national_champions": "国之重器公司",
    "recent_ipos": "次新股查看",
}
DEFAULT_CUSTOM_SOURCE = "info_gap"
BITCOIN_CODE = "BTCUSD"
BITCOIN_NAME = "比特币"
BITCOIN_TRADINGVIEW_URL = "https://www.tradingview.com/chart/nboywMkl/?symbol=COINBASE%3ABTCUSD"

app = Flask(__name__)
_db_ready = False


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(MARKET_BREAK_RATE_SCHEMA)
        conn.execute(MARKET_AMOUNT_SCHEMA)
        conn.executescript(
            """
            create table if not exists companies (
                code text primary key,
                name text not null,
                deleted integer not null default 0,
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );

            create table if not exists gap_stocks (
                id integer primary key autoincrement,
                period text not null,
                code text not null,
                name text not null,
                announcement_date text,
                gap_gain real,
                source_file text,
                created_at text not null default current_timestamp,
                unique(period, code)
            );

            create table if not exists company_info (
                code text primary key,
                main_business text,
                product_names text,
                business_scope text,
                registered_address text,
                office_address text,
                raw_json text,
                fetched_at text,
                manual_updated_at text
            );

            create table if not exists shareholders (
                id integer primary key autoincrement,
                code text not null,
                report_period text not null,
                holder_name text,
                holder_type text,
                shares text,
                ratio text,
                change_text text,
                raw_json text,
                fetched_at text,
                unique(code, report_period, holder_name)
            );

            create table if not exists annual_reports (
                id integer primary key autoincrement,
                code text not null,
                title text not null,
                url text,
                announcement_date text,
                raw_json text,
                fetched_at text,
                unique(code, title, announcement_date)
            );

            create table if not exists research_reports (
                id integer primary key autoincrement,
                code text not null,
                title text not null,
                institution text,
                report_date text,
                pdf_url text,
                raw_json text,
                fetched_at text,
                unique(code, title, institution, report_date)
            );

            create table if not exists notes (
                code text primary key,
                bullish_points text default '',
                other_notes text default '',
                updated_at text not null default current_timestamp
            );

            create table if not exists note_pages (
                id integer primary key autoincrement,
                code text not null,
                title text not null default 'Untitled Note',
                content_html text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );

            create table if not exists interested_stocks (
                code text primary key,
                name text not null,
                created_at text not null default current_timestamp
            );

            create table if not exists categorized_stocks (
                category text not null,
                code text not null,
                name text not null,
                created_at text not null default current_timestamp,
                primary key(category, code)
            );

            create table if not exists category_notes (
                category text primary key,
                content_html text not null default '',
                updated_at text not null default current_timestamp
            );

            create table if not exists special_notes (
                item_key text primary key,
                content_html text not null default '',
                updated_at text not null default current_timestamp
            );
            """
        )
        existing_columns = {
            row[1] for row in conn.execute("pragma table_info(company_info)").fetchall()
        }
        if "registered_address" not in existing_columns:
            conn.execute("alter table company_info add column registered_address text")
        if "office_address" not in existing_columns:
            conn.execute("alter table company_info add column office_address text")
        conn.commit()


def migrate_interested_stocks():
    conn = db()
    exists = conn.execute(
        "select name from sqlite_master where type='table' and name='interested_stocks'"
    ).fetchone()
    if not exists:
        return 0
    rows = conn.execute("select code, name, created_at from interested_stocks").fetchall()
    migrated = 0
    for row in rows:
        conn.execute(
            """
            insert or ignore into categorized_stocks(category, code, name, created_at)
            values('interested', ?, ?, ?)
            """,
            (row["code"], row["name"], row["created_at"]),
        )
        migrated += 1
    if migrated:
        conn.commit()
    return migrated


def ensure_special_assets():
    db().execute(
        """
        insert into categorized_stocks(category, code, name, created_at)
        values('national_champions', ?, ?, current_timestamp)
        on conflict(category, code) do update set name=excluded.name
        """,
        (BITCOIN_CODE, BITCOIN_NAME),
    )
    db().commit()


def legacy_text_to_html(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "".join(f"<p>{line}</p>" for line in lines)


def linkify_note_html(content_html):
    if not content_html:
        return ""

    def replace_text(match):
        text = match.group(0)
        if text.startswith("<"):
            return text
        url_pattern = re.compile(r"((?:https?://|www\.)[^\s<]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s<]*)")

        def repl(url_match):
            raw_url = url_match.group(0)
            clean_url = raw_url.rstrip(".,;:!?)")
            suffix = raw_url[len(clean_url):]
            href = clean_url if re.match(r"^https?://", clean_url, re.I) else f"https://{clean_url}"
            return f'<a href="{href}" target="_blank" rel="noreferrer">{clean_url}</a>{suffix}'

        return url_pattern.sub(repl, text)

    parts = re.split(r"(<a\b[^>]*>.*?</a>|<[^>]+>)", content_html, flags=re.I | re.S)
    return "".join(replace_text(re.match(r".*", part, re.S)) if part and not part.startswith("<") else part for part in parts)


def migrate_legacy_notes():
    conn = db()
    rows = conn.execute(
        """
        select code, bullish_points, other_notes
        from notes
        where coalesce(bullish_points, '') <> '' or coalesce(other_notes, '') <> ''
        """
    ).fetchall()
    created = 0
    for row in rows:
        for title, text in (("Bullish", row["bullish_points"]), ("Other", row["other_notes"])):
            content = legacy_text_to_html(text)
            if not content:
                continue
            exists = conn.execute(
                "select 1 from note_pages where code=? and title=? limit 1",
                (row["code"], title),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                insert into note_pages(code, title, content_html, created_at, updated_at)
                values(?, ?, ?, current_timestamp, current_timestamp)
                """,
                (row["code"], title, content),
            )
            created += 1
    if created:
        conn.commit()
    return created


@app.before_request
def ensure_db_ready():
    global _db_ready
    if not _db_ready:
        init_db()
        migrate_interested_stocks()
        ensure_special_assets()
        migrate_legacy_notes()
        backfill_research_urls()
        repair_interested_stock_names()
        _db_ready = True


def period_from_filename(path):
    match = re.search(r"gapstock_?(\d{4})(0331|0630|0930|1231)\.json$", path.name)
    return f"{match.group(1)}{match.group(2)}" if match else None


def gapstock_files():
    paths = list(GAPSTOCK_DIR.glob("gapstock*.json"))
    if not paths:
        paths = list(BASE_DIR.glob("gapstock*.json"))
    return sorted(paths)


def detected_gapstock_sources():
    sources = {}
    for path in gapstock_files():
        period = period_from_filename(path)
        if period:
            sources[period] = path
    return sources


def active_period():
    if not ACTIVE_PERIOD_PATH.exists():
        return ""
    try:
        data = json.loads(ACTIVE_PERIOD_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("active_period", "")).strip()


def source_metadata(period):
    path = detected_gapstock_sources().get(period)
    if not path:
        return None
    modified_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "file": str(path.relative_to(BASE_DIR)),
        "modified_at": modified_at,
        "is_active": period == active_period(),
    }


def years_for_selector():
    current_year = date.today().year
    return [str(year) for year in range(current_year + 2, current_year - 11, -1)]


def quarter_options():
    return [{"value": quarter, "label": QUARTER_LABELS[quarter]} for quarter in QUARTERS]


def import_gap_files():
    conn = db()
    imported = []
    for path in gapstock_files():
        period = period_from_filename(path)
        if not period:
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        for item in rows:
            code = str(item.get("股票代码", "")).strip()
            name = str(item.get("股票简称", "")).strip()
            if not code or not name:
                continue
            conn.execute(
                """
                insert into companies(code, name) values(?, ?)
                on conflict(code) do update set
                    name=excluded.name,
                    updated_at=current_timestamp
                """,
                (code, name),
            )
            conn.execute(
                """
                insert into gap_stocks(period, code, name, announcement_date, gap_gain, source_file)
                values(?, ?, ?, ?, ?, ?)
                on conflict(period, code) do update set
                    name=excluded.name,
                    announcement_date=excluded.announcement_date,
                    gap_gain=excluded.gap_gain,
                    source_file=excluded.source_file
                """,
                (
                    period,
                    code,
                    name,
                    item.get("公告日期"),
                    float(item.get("跳空涨幅") or 0),
                    str(path.relative_to(BASE_DIR)),
                ),
            )
        imported.append({"period": period, "count": len(rows), "file": path.name})
    conn.commit()
    return imported


def available_periods():
    rows = db().execute(
        "select period, count(*) as count from gap_stocks group by period order by period desc"
    ).fetchall()
    return [dict(row) for row in rows]


def current_period():
    sources = detected_gapstock_sources()
    if sources:
        return sorted(sources, reverse=True)[0]
    periods = available_periods()
    return periods[0]["period"] if periods else f"{date.today().year}0331"


def normalize_code(code):
    return re.sub(r"\D", "", code or "")


def code_unified(code):
    pure = normalize_code(code)
    if pure.startswith(("600", "601", "603", "605", "688")):
        return f"sh.{pure}"
    if pure.startswith(("000", "001", "002", "300", "301")):
        return f"sz.{pure}"
    return None


def code_name_map():
    if not CODE_NAME_PATH.exists():
        return {}
    try:
        data = json.loads(CODE_NAME_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(code): str(name).strip() for code, name in data.items() if str(name).strip()}


def name_from_code_map(code):
    unified = code_unified(code)
    if not unified:
        return ""
    return code_name_map().get(unified, "")


def save_code_name_mapping(code, name):
    """Persist a newly verified stock code/name mapping for future lookups."""
    unified = code_unified(code)
    clean_name = str(name or "").strip()
    if not unified or not clean_name:
        return False

    mapping = code_name_map()
    if mapping.get(unified) == clean_name:
        return True
    mapping[unified] = clean_name

    temporary_path = CODE_NAME_PATH.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(CODE_NAME_PATH)
    except OSError:
        return False
    return True


def name_from_research_report(code):
    """Use AkShare research reports as a last-resort code/name lookup."""
    pure = normalize_code(code)
    if not pure:
        return ""
    try:
        import akshare as ak

        records = ak.stock_research_report_em(symbol=pure).to_dict("records")
    except Exception:
        return ""

    for record in records:
        record_code = normalize_code(record.get("股票代码"))
        record_name = str(record.get("股票简称") or "").strip()
        if record_code == pure and record_name:
            save_code_name_mapping(record_code, record_name)
            return record_name
    return ""


def find_company_by_input(value, manual_name=""):
    query = (value or "").strip()
    manual_name = (manual_name or "").strip()
    if not query:
        return None
    pure = normalize_code(query)
    if pure:
        row = db().execute("select code, name from companies where code=?", (pure,)).fetchone()
        if row and row["name"] != row["code"]:
            return dict(row)
        gap_row = db().execute(
            "select code, name from gap_stocks where code=? order by period desc limit 1", (pure,)
        ).fetchone()
        if gap_row:
            return dict(gap_row)
        mapped_name = name_from_code_map(pure)
        if mapped_name:
            return {"code": pure, "name": mapped_name}
        research_name = name_from_research_report(pure)
        if research_name:
            return {"code": pure, "name": research_name}
        if manual_name:
            return {"code": pure, "name": manual_name}
        return None
    row = db().execute(
        "select code, name from companies where name=? collate nocase limit 1", (query,)
    ).fetchone()
    if row:
        return dict(row)
    gap_row = db().execute(
        "select code, name from gap_stocks where name like ? order by period desc limit 1",
        (f"%{query}%",),
    ).fetchone()
    if gap_row:
        return dict(gap_row)
    for prefixed_code_value, mapped_name in code_name_map().items():
        if mapped_name == query:
            return {"code": normalize_code(prefixed_code_value), "name": mapped_name}
    return None


def is_custom_source(source):
    return source in CUSTOM_STOCK_SOURCES


def stock_source_label(source):
    return CUSTOM_STOCK_SOURCES.get(source, "Net Profit Gap Stocks")


def custom_stock_rows(category):
    return db().execute(
        """
        select c.code, c.name, latest.announcement_date, latest.gap_gain
        from categorized_stocks c
        left join (
            select g1.code, g1.announcement_date, g1.gap_gain
            from gap_stocks g1
            join (
                select code, max(period) as max_period
                from gap_stocks
                group by code
            ) g2 on g1.code = g2.code and g1.period = g2.max_period
        ) latest on latest.code = c.code
        where c.category = ?
        order by c.created_at desc
        """,
        (category,),
    ).fetchall()


def category_note(source):
    if not is_custom_source(source):
        return None
    return db().execute(
        "select * from category_notes where category=?",
        (source,),
    ).fetchone()


def special_note(item_key):
    return db().execute(
        "select * from special_notes where item_key=?",
        (item_key,),
    ).fetchone()


def interested_stock_rows():
    return custom_stock_rows("interested")


def repair_interested_stock_names():
    company_rows = db().execute(
        """
        select code, name
        from companies
        where name = code or name = ''
        """
    ).fetchall()
    changed = False
    for row in company_rows:
        mapped_name = name_from_code_map(row["code"])
        if not mapped_name:
            continue
        db().execute(
            "update companies set name=?, updated_at=current_timestamp where code=?",
            (mapped_name, row["code"]),
        )
        changed = True

    rows = db().execute(
        """
        select code, name
        from categorized_stocks
        where name = code or name = ''
        """
    ).fetchall()
    for row in rows:
        mapped_name = name_from_code_map(row["code"])
        if not mapped_name:
            continue
        db().execute(
            "update categorized_stocks set name=? where code=?",
            (mapped_name, row["code"]),
        )
        db().execute(
            """
            insert into companies(code, name) values(?, ?)
            on conflict(code) do update set name=excluded.name, updated_at=current_timestamp
            """,
            (row["code"], mapped_name),
        )
        changed = True
    if changed:
        db().commit()


def company_redirect_url(code, anchor=""):
    source = request.args.get("source", "gap")
    args = {"code": code, "source": source}
    if source == "gap":
        year = request.args.get("year")
        quarter = request.args.get("quarter")
        if year and quarter:
            args.update({"year": year, "quarter": quarter, "sort": request.args.get("sort", "desc")})
    url = url_for("company", **args)
    return f"{url}{anchor}"


def prefixed_code(code):
    pure = normalize_code(code)
    if pure.startswith(("6", "9")):
        return f"sh{pure}"
    if pure.startswith(("4", "8")):
        return f"bj{pure}"
    return f"sz{pure}"


def period_candidates(today=None):
    today = today or date.today()
    candidates = []
    for year in range(today.year, today.year - 6, -1):
        for quarter in reversed(QUARTERS):
            period = f"{year}{quarter}"
            period_date = datetime.strptime(period, "%Y%m%d").date()
            if period_date <= today:
                candidates.append(period)
    return candidates


def recent_annual_window(today=None):
    today = today or date.today()
    return f"{today.year - 5}0101", f"{today.year}1231"


def pick(record, *names):
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return ""


def to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def format_percent(value):
    number = to_float(value)
    if number is None:
        return ""
    return f"{number:.2f}%"


def shareholder_remark(raw, change_value):
    text = " ".join(str(raw.get(key, "")) for key in ("增减", "变动", "股东类型", "股份类型"))
    if "新进" in text:
        return "New"
    if "不变" in text:
        return "Unchanged"
    number = to_float(change_value)
    if number is None:
        return ""
    if number > 0:
        return "Increased"
    if number < 0:
        return "Decreased"
    return "Unchanged"


def shareholder_view(holder):
    raw = {}
    if holder["raw_json"]:
        try:
            raw = json.loads(holder["raw_json"])
        except json.JSONDecodeError:
            raw = {}
    ratio = pick(raw, "占总股本持股比例", "持股比例", "占总股本比例") or holder["ratio"]
    change_ratio = pick(raw, "变动比率", "变动比例") or holder["change_text"]
    return {
        "name": holder["holder_name"],
        "holding_ratio": format_percent(ratio),
        "change_ratio": format_percent(change_ratio),
        "remark": shareholder_remark(raw, change_ratio),
    }


def research_pdf_url(record):
    direct = pick(record, "报告PDF链接", "报告pdf链接", "pdf链接", "报告链接", "链接", "PDF链接")
    if direct:
        return direct
    for key, value in record.items():
        key_text = str(key).lower()
        if value and ("pdf" in key_text or "链接" in str(key) or "url" in key_text):
            return value
    return ""


def backfill_research_urls():
    conn = db()
    rows = conn.execute(
        "select id, raw_json from research_reports where coalesce(pdf_url, '') = '' and coalesce(raw_json, '') <> ''"
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        pdf_url = research_pdf_url(raw)
        if pdf_url:
            conn.execute("update research_reports set pdf_url=? where id=?", (pdf_url, row["id"]))
            changed += 1
    if changed:
        conn.commit()
    return changed


def fetch_company_data(code):
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError("AkShare is not installed in this Python environment.") from exc

    conn = db()
    pure = normalize_code(code)
    fetched_at = datetime.now().isoformat(timespec="seconds")

    info_df = ak.stock_zyjs_ths(symbol=pure)
    info_records = info_df.to_dict("records")
    info = info_records[0] if info_records else {}
    try:
        profile_df = ak.stock_profile_cninfo(symbol=pure)
        profile_records = profile_df.to_dict("records")
    except Exception:
        profile_records = []
    profile = profile_records[0] if profile_records else {}
    conn.execute(
        """
        insert into company_info(
            code, main_business, product_names, business_scope,
            registered_address, office_address, raw_json, fetched_at
        )
        values(?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(code) do update set
            main_business=coalesce(company_info.main_business, excluded.main_business),
            product_names=coalesce(company_info.product_names, excluded.product_names),
            business_scope=coalesce(company_info.business_scope, excluded.business_scope),
            registered_address=coalesce(company_info.registered_address, excluded.registered_address),
            office_address=coalesce(company_info.office_address, excluded.office_address),
            raw_json=excluded.raw_json,
            fetched_at=excluded.fetched_at
        """,
        (
            pure,
            pick(info, "主营业务"),
            pick(info, "产品名称"),
            pick(info, "经营范围"),
            pick(profile, "注册地址"),
            pick(profile, "办公地址"),
            json.dumps(
                {"business": info_records, "profile": profile_records},
                ensure_ascii=False,
                default=str,
            ),
            fetched_at,
        ),
    )

    shareholder_records = []
    latest_period = None
    for report_period in period_candidates():
        try:
            holder_df = ak.stock_gdfx_top_10_em(symbol=prefixed_code(pure), date=report_period)
            shareholder_records = holder_df.to_dict("records")
        except Exception:
            shareholder_records = []
        if shareholder_records:
            latest_period = report_period
            break

    if latest_period:
        conn.execute("delete from shareholders where code=? and report_period=?", (pure, latest_period))
        for holder in shareholder_records[:10]:
            conn.execute(
                """
                insert or ignore into shareholders(
                    code, report_period, holder_name, holder_type, shares, ratio, change_text, raw_json, fetched_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pure,
                    latest_period,
                    pick(holder, "股东名称", "股东名次"),
                    pick(holder, "股东性质", "股东类型"),
                    str(pick(holder, "持股数", "持股数量", "期末持股-数量")),
                    str(pick(holder, "占总股本持股比例", "持股比例", "占总股本比例", "期末持股-比例")),
                    str(pick(holder, "变动比率", "变动比例", "增减", "持股变动")),
                    json.dumps(holder, ensure_ascii=False, default=str),
                    fetched_at,
                ),
            )

    start_date, end_date = recent_annual_window()
    try:
        annual_df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=pure,
            market="沪深京",
            category="年报",
            start_date=start_date,
            end_date=end_date,
        )
        annual_records = annual_df.to_dict("records")
    except Exception:
        annual_records = []

    for report in annual_records:
        title = pick(report, "公告标题", "公告名称", "标题")
        if not title:
            continue
        conn.execute(
            """
            insert or ignore into annual_reports(code, title, url, announcement_date, raw_json, fetched_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (
                pure,
                title,
                pick(report, "公告链接", "公告网址", "链接", "url"),
                str(pick(report, "公告日期", "日期")),
                json.dumps(report, ensure_ascii=False, default=str),
                fetched_at,
            ),
        )

    try:
        research_df = ak.stock_research_report_em(symbol=pure)
        research_records = research_df.to_dict("records")
    except Exception:
        research_records = []

    for report in research_records:
        title = pick(report, "报告名称", "标题")
        if not title:
            continue
        pdf_url = research_pdf_url(report)
        conn.execute(
            """
            insert into research_reports(code, title, institution, report_date, pdf_url, raw_json, fetched_at)
            values(?, ?, ?, ?, ?, ?, ?)
            on conflict(code, title, institution, report_date) do update set
                pdf_url=case
                    when coalesce(research_reports.pdf_url, '') = '' then excluded.pdf_url
                    else research_reports.pdf_url
                end,
                raw_json=excluded.raw_json,
                fetched_at=excluded.fetched_at
            """,
            (
                pure,
                title,
                pick(report, "机构", "评级机构"),
                str(pick(report, "日期", "报告日期")),
                pdf_url,
                json.dumps(report, ensure_ascii=False, default=str),
                fetched_at,
            ),
        )
    conn.commit()
    backfilled_urls = backfill_research_urls()
    return {
        "info": len(info_records),
        "profile": len(profile_records),
        "shareholder_period": latest_period,
        "shareholders": len(shareholder_records),
        "annual_reports": len(annual_records),
        "research_reports": len(research_records),
        "backfilled_research_urls": backfilled_urls,
    }


@app.route("/")
def index():
    import_gap_files()
    backfill_research_urls()
    repair_interested_stock_names()
    source = request.args.get("source", "gap")
    if source != "gap" and not is_custom_source(source):
        source = "gap"
    selected_year = request.args.get("year")
    selected_quarter = request.args.get("quarter")
    if selected_year and selected_quarter:
        period = f"{selected_year}{selected_quarter}"
    else:
        period = request.args.get("period") or current_period()
        selected_year = period[:4]
        selected_quarter = period[4:]
    sort = request.args.get("sort", "desc")
    order = "asc" if sort == "asc" else "desc"
    source_available = period in detected_gapstock_sources()
    source_meta = source_metadata(period)
    rows = []
    if is_custom_source(source):
        source_available = True
        rows = custom_stock_rows(source)
    elif source_available:
        rows = db().execute(
            f"""
            select g.*, c.deleted
            from gap_stocks g
            join companies c on c.code = g.code
            where g.period = ? and c.deleted = 0
            order by g.gap_gain {order}, g.announcement_date desc
            """,
            (period,),
        ).fetchall()
    return render_template(
        "index.html",
        rows=rows,
        periods=available_periods(),
        period=period,
        selected_year=selected_year,
        selected_quarter=selected_quarter,
        years=years_for_selector(),
        quarter_options=quarter_options(),
        source_available=source_available,
        source_meta=source_meta,
        source=source,
        source_label=stock_source_label(source),
        custom_sources=CUSTOM_STOCK_SOURCES,
        sort=sort,
        message=request.args.get("message", ""),
    )


@app.get("/sentiment")
def sentiment():
    return render_template("sentiment.html", indicator=None)


@app.get("/sentiment/amount")
def sentiment_amount():
    rows = db().execute("SELECT * FROM market_amount ORDER BY date DESC").fetchall()
    return render_template("sentiment.html", indicator="amount", rows=rows)


@app.get("/sentiment/break-rate")
def sentiment_break_rate():
    rows = db().execute("SELECT * FROM market_break_rate ORDER BY date DESC").fetchall()
    return render_template("sentiment.html", indicator="break_rate", rows=rows)


@app.route("/company/<code>")
def company(code):
    pure = normalize_code(code)
    backfill_research_urls()
    selected_year = request.args.get("year")
    selected_quarter = request.args.get("quarter")
    sort = request.args.get("sort", "desc")
    source = request.args.get("source", "gap")
    if source != "gap" and not is_custom_source(source):
        source = "gap"
    selected_period = f"{selected_year}{selected_quarter}" if selected_year and selected_quarter else ""
    if is_custom_source(source):
        back_url = url_for("index", source=source)
    else:
        back_url = url_for("index", year=selected_year, quarter=selected_quarter, sort=sort) if selected_period else url_for("index")
    stock = db().execute("select * from companies where code=?", (pure,)).fetchone()
    if not stock:
        return redirect(back_url)

    if selected_period:
        gap = db().execute(
            "select * from gap_stocks where code=? and period=? limit 1", (pure, selected_period)
        ).fetchone()
    else:
        gap = db().execute(
            "select * from gap_stocks where code=? order by period desc limit 1", (pure,)
        ).fetchone()
    info = db().execute("select * from company_info where code=?", (pure,)).fetchone()
    holder_period = db().execute(
        "select max(report_period) as period from shareholders where code=?", (pure,)
    ).fetchone()["period"]
    holder_rows = db().execute(
        "select * from shareholders where code=? and report_period=? order by id limit 10",
        (pure, holder_period or ""),
    ).fetchall()
    holders = [shareholder_view(holder) for holder in holder_rows]
    annual_reports = db().execute(
        "select * from annual_reports where code=? order by announcement_date desc limit 20", (pure,)
    ).fetchall()
    research_reports = db().execute(
        "select * from research_reports where code=? order by report_date desc limit 50", (pure,)
    ).fetchall()
    note_pages = [
        dict(row)
        for row in db().execute(
        "select * from note_pages where code=? order by updated_at desc, id desc", (pure,)
        ).fetchall()
    ]
    return render_template(
        "company.html",
        stock=stock,
        gap=gap,
        info=info,
        holder_period=holder_period,
        holders=holders,
        annual_reports=annual_reports,
        research_reports=research_reports,
        note_pages=note_pages,
        back_url=back_url,
        selected_year=selected_year,
        selected_quarter=selected_quarter,
        sort=sort,
        source=source,
    )


@app.post("/company/<code>/info")
def save_info(code):
    pure = normalize_code(code)
    db().execute(
        """
        insert into company_info(
            code, main_business, product_names, business_scope,
            registered_address, office_address, manual_updated_at
        )
        values(?, ?, ?, ?, ?, ?, current_timestamp)
        on conflict(code) do update set
            main_business=excluded.main_business,
            product_names=excluded.product_names,
            business_scope=excluded.business_scope,
            registered_address=excluded.registered_address,
            office_address=excluded.office_address,
            manual_updated_at=current_timestamp
        """,
        (
            pure,
            request.form.get("main_business", ""),
            request.form.get("product_names", ""),
            request.form.get("business_scope", ""),
            request.form.get("registered_address", ""),
            request.form.get("office_address", ""),
        ),
    )
    db().commit()
    return redirect(company_redirect_url(pure, "#info"))


@app.post("/company/<code>/note-pages")
def save_note_page(code):
    pure = normalize_code(code)
    page_id = request.form.get("page_id")
    title = request.form.get("title", "").strip() or "Untitled Note"
    content_html = linkify_note_html(request.form.get("content_html", ""))
    if page_id:
        db().execute(
            """
            update note_pages
            set title=?, content_html=?, updated_at=current_timestamp
            where id=? and code=?
            """,
            (title, content_html, page_id, pure),
        )
    else:
        db().execute(
            """
            insert into note_pages(code, title, content_html, created_at, updated_at)
            values(?, ?, ?, current_timestamp, current_timestamp)
            """,
            (pure, title, content_html),
        )
    db().commit()
    return redirect(company_redirect_url(pure, "#notes"))


@app.post("/company/<code>/note-pages/delete")
def delete_note_page(code):
    pure = normalize_code(code)
    page_id = request.form.get("page_id")
    if page_id:
        db().execute("delete from note_pages where id=? and code=?", (page_id, pure))
        db().commit()
    return redirect(company_redirect_url(pure, "#notes"))


@app.post("/company/<code>/delete")
def delete_company(code):
    pure = normalize_code(code)
    source = request.args.get("source", "gap")
    if is_custom_source(source):
        db().execute("delete from categorized_stocks where category=? and code=?", (source, pure))
        db().commit()
        return redirect(url_for("index", source=source))
    db().execute("update companies set deleted=1, updated_at=current_timestamp where code=?", (pure,))
    db().commit()
    year = request.args.get("year")
    quarter = request.args.get("quarter")
    if year and quarter:
        return redirect(url_for("index", source="gap", year=year, quarter=quarter, sort=request.args.get("sort", "desc")))
    return redirect(url_for("index", source="gap"))


@app.post("/company/<code>/restore")
def restore_company(code):
    pure = normalize_code(code)
    db().execute("update companies set deleted=0, updated_at=current_timestamp where code=?", (pure,))
    db().commit()
    return redirect(company_redirect_url(pure))


@app.post("/company/<code>/refresh")
def refresh_company(code):
    pure = normalize_code(code)
    try:
        result = fetch_company_data(pure)
        result["backfilled_research_urls"] = result.get("backfilled_research_urls", 0) + backfill_research_urls()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/custom-stocks/add")
def add_custom_stock():
    source = request.form.get("source", DEFAULT_CUSTOM_SOURCE)
    if not is_custom_source(source):
        source = DEFAULT_CUSTOM_SOURCE
    match = find_company_by_input(
        request.form.get("stock_query", ""),
        request.form.get("manual_name", ""),
    )
    if not match:
        return redirect(url_for("index", source=source, message="Name not found. Please enter the company name manually."))
    db().execute(
        """
        insert into companies(code, name) values(?, ?)
        on conflict(code) do update set name=excluded.name, updated_at=current_timestamp
        """,
        (match["code"], match["name"]),
    )
    db().execute(
        """
        insert into categorized_stocks(category, code, name, created_at)
        values(?, ?, ?, current_timestamp)
        on conflict(category, code) do update set name=excluded.name
        """,
        (source, match["code"], match["name"]),
    )
    db().commit()
    return redirect(url_for("index", source=source))


@app.post("/custom-stocks/<source>/<code>/delete")
def delete_custom_stock(source, code):
    if not is_custom_source(source):
        source = DEFAULT_CUSTOM_SOURCE
    pure = normalize_code(code)
    db().execute("delete from categorized_stocks where category=? and code=?", (source, pure))
    db().commit()
    return redirect(url_for("index", source=source))


@app.route("/category/<source>/general-note")
def category_general_note(source):
    if not is_custom_source(source):
        return redirect(url_for("index", source=DEFAULT_CUSTOM_SOURCE))
    note = category_note(source)
    return render_template(
        "category_note.html",
        source=source,
        source_label=stock_source_label(source),
        note=note,
        back_url=url_for("index", source=source),
    )


@app.post("/category/<source>/general-note")
def save_category_general_note(source):
    if not is_custom_source(source):
        return redirect(url_for("index", source=DEFAULT_CUSTOM_SOURCE))
    content_html = linkify_note_html(request.form.get("content_html", ""))
    db().execute(
        """
        insert into category_notes(category, content_html, updated_at)
        values(?, ?, current_timestamp)
        on conflict(category) do update set
            content_html=excluded.content_html,
            updated_at=current_timestamp
        """,
        (source, content_html),
    )
    db().commit()
    return redirect(url_for("category_general_note", source=source))


@app.route("/bitcoin")
def bitcoin_detail():
    return render_template(
        "bitcoin.html",
        back_url=url_for("index", source="national_champions"),
        bitcoin_name=BITCOIN_NAME,
        bitcoin_code=BITCOIN_CODE,
        tradingview_url=BITCOIN_TRADINGVIEW_URL,
        note=special_note("bitcoin"),
    )


@app.post("/bitcoin/note")
def save_bitcoin_note():
    content_html = linkify_note_html(request.form.get("content_html", ""))
    db().execute(
        """
        insert into special_notes(item_key, content_html, updated_at)
        values('bitcoin', ?, current_timestamp)
        on conflict(item_key) do update set
            content_html=excluded.content_html,
            updated_at=current_timestamp
        """,
        (content_html,),
    )
    db().commit()
    return redirect(url_for("bitcoin_detail"))


@app.post("/interested/add")
def add_interested_stock():
    return redirect(url_for("index", source=DEFAULT_CUSTOM_SOURCE))


@app.post("/interested/<code>/delete")
def delete_interested_stock(code):
    return redirect(url_for("index", source=DEFAULT_CUSTOM_SOURCE))


@app.post("/import")
def import_now():
    return jsonify({"ok": True, "imported": import_gap_files()})


if __name__ == "__main__":
    with app.app_context():
        init_db()
        migrate_interested_stocks()
        ensure_special_assets()
        import_gap_files()
    app.run(debug=False, port=5000, use_reloader=False)
