import json
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "finance_guide.sqlite3"
OUT_DIR = BASE_DIR / "docs"


def rows(conn, query, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def active_period():
    path = BASE_DIR / "data" / "active_period.json"
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("active_period", "")
    except json.JSONDecodeError:
        return ""


def build_payload():
    conn = sqlite3.connect(DB_PATH)
    periods = rows(
        conn,
        """
        select period, count(*) as count
        from gap_stocks
        group by period
        order by period desc
        """,
    )
    gap_stocks = rows(
        conn,
        """
        select g.period, g.code, g.name, g.announcement_date, g.gap_gain, g.source_file
        from gap_stocks g
        join companies c on c.code = g.code
        where c.deleted = 0
        order by g.period desc, g.gap_gain desc
        """,
    )
    companies = rows(conn, "select code, name from companies where deleted = 0")
    info = {
        row["code"]: row
        for row in rows(
            conn,
            """
            select code, main_business, product_names, business_scope,
                   registered_address, office_address, fetched_at
            from company_info
            """,
        )
    }
    shareholders = {}
    for row in rows(
        conn,
        """
        select code, report_period, holder_name, ratio, change_text, raw_json
        from shareholders
        order by code, report_period desc, id
        """,
    ):
        shareholders.setdefault(row["code"], []).append(row)
    annual_reports = {}
    for row in rows(
        conn,
        """
        select code, title, url, announcement_date
        from annual_reports
        order by announcement_date desc
        """,
    ):
        annual_reports.setdefault(row["code"], []).append(row)
    research_reports = {}
    for row in rows(
        conn,
        """
        select code, title, institution, report_date, pdf_url
        from research_reports
        order by report_date desc
        """,
    ):
        research_reports.setdefault(row["code"], []).append(row)

    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "activePeriod": active_period(),
        "periods": periods,
        "gapStocks": gap_stocks,
        "companies": {row["code"]: row for row in companies},
        "info": info,
        "shareholders": shareholders,
        "annualReports": annual_reports,
        "researchReports": research_reports,
    }


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Net Profit Gap Stocks Demo</title>
  <style>
    :root {
      --bg: #0b0e11;
      --panel: #181a20;
      --soft: #1e2329;
      --text: #eaecef;
      --muted: #848e9c;
      --line: #2b3139;
      --yellow: #f0b90b;
      --green: #0ecb81;
      --red: #f6465d;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    a { color: inherit; text-decoration: none; }
    .shell { width: min(100%, 1080px); margin: 0 auto; padding: 28px 18px 64px; }
    .hero { display: grid; gap: 14px; padding: 28px 0; }
    .eyebrow { margin: 0; color: var(--yellow); font-weight: 800; }
    h1 { margin: 0; font-size: clamp(34px, 7vw, 72px); line-height: 1; letter-spacing: 0; }
    .subtitle { max-width: 760px; color: var(--muted); font-size: 18px; line-height: 1.55; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0 26px; }
    .stat, .panel, .card { border: 1px solid var(--line); border-radius: 16px; background: var(--panel); }
    .stat { padding: 16px; }
    .stat strong { display: block; color: var(--yellow); font-size: 26px; }
    .controls { display: grid; grid-template-columns: 1fr 1fr auto; gap: 12px; padding: 16px; margin-bottom: 16px; }
    label { display: grid; gap: 8px; color: var(--muted); font-weight: 800; }
    select, button { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: var(--soft); color: var(--text); font: inherit; font-weight: 800; }
    button { cursor: pointer; }
    button.primary { border: 0; background: var(--yellow); color: #181a20; }
    .badge { display: inline-flex; width: fit-content; padding: 7px 10px; border-radius: 999px; background: #2b2410; color: var(--yellow); font-size: 12px; font-weight: 900; }
    .grid { display: grid; grid-template-columns: 360px 1fr; gap: 16px; align-items: start; }
    .list { display: grid; gap: 10px; max-height: 72vh; overflow: auto; padding-right: 4px; }
    .stock { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 14px; cursor: pointer; }
    .stock:hover, .stock.active { border-color: var(--yellow); }
    .stock small, .muted { color: var(--muted); }
    .gain { padding: 7px 9px; border-radius: 9px; background: #2b2410; color: var(--yellow); font-weight: 900; }
    .detail { display: grid; gap: 14px; }
    .panel { padding: 18px; }
    .panel h2 { margin: 0 0 12px; }
    .info-box { display: grid; gap: 10px; }
    .info-box div, .report { padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: #0f1318; }
    .info-box span, .report span { display: block; color: var(--muted); font-size: 13px; margin-bottom: 5px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px; border-bottom: 1px solid var(--line); text-align: left; font-size: 14px; }
    th { color: var(--muted); }
    .reports { display: grid; gap: 8px; max-height: 340px; overflow: auto; }
    .report:hover { border-color: var(--yellow); }
    .empty { padding: 24px; border: 1px dashed var(--line); border-radius: 14px; color: var(--muted); text-align: center; }
    footer { margin-top: 32px; color: var(--muted); font-size: 13px; }
    @media (max-width: 860px) { .stats, .grid, .controls { grid-template-columns: 1fr; } .list { max-height: none; } }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Investment Research Notebook Demo</p>
      <h1>Net Profit Gap Stocks</h1>
      <p class="subtitle">A static portfolio demo for tracking net profit gap stocks, company fundamentals, shareholder changes, annual reports, research reports, and structured research notes.</p>
      <span class="badge" id="activeBadge"></span>
    </section>

    <section class="stats">
      <div class="stat"><strong id="statPeriods">0</strong><span>Periods</span></div>
      <div class="stat"><strong id="statStocks">0</strong><span>Gap Stock Rows</span></div>
      <div class="stat"><strong id="statReports">0</strong><span>Research Reports</span></div>
      <div class="stat"><strong id="statGenerated">-</strong><span>Generated</span></div>
    </section>

    <section class="panel controls">
      <label>Year <select id="year"></select></label>
      <label>Quarter <select id="quarter"></select></label>
      <button class="primary" id="apply">Apply</button>
    </section>

    <section class="grid">
      <div class="list" id="stockList"></div>
      <div class="detail" id="detail"></div>
    </section>
    <footer>This public demo excludes private note content. Source data is cached from local JSON and public market disclosure interfaces.</footer>
  </main>
  <script id="payload" type="application/json">__PAYLOAD__</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload").textContent);
    const quarterLabels = { "0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4" };
    const yearSelect = document.getElementById("year");
    const quarterSelect = document.getElementById("quarter");
    const stockList = document.getElementById("stockList");
    const detail = document.getElementById("detail");
    const periods = new Set(payload.periods.map(p => p.period));
    const latestPeriod = payload.periods[0]?.period || payload.activePeriod;

    document.getElementById("statPeriods").textContent = payload.periods.length;
    document.getElementById("statStocks").textContent = payload.gapStocks.length;
    document.getElementById("statReports").textContent = Object.values(payload.researchReports).reduce((sum, rows) => sum + rows.length, 0);
    document.getElementById("statGenerated").textContent = payload.generatedAt.slice(5, 10);
    document.getElementById("activeBadge").textContent = payload.activePeriod ? `Active Reporting Season: ${payload.activePeriod.slice(0, 4)} ${quarterLabels[payload.activePeriod.slice(4)]}` : "Static Demo";

    const currentYear = new Date().getFullYear();
    for (let y = currentYear + 2; y >= currentYear - 10; y--) {
      yearSelect.add(new Option(String(y), String(y), false, String(y) === latestPeriod?.slice(0, 4)));
    }
    for (const [value, label] of Object.entries(quarterLabels)) {
      quarterSelect.add(new Option(label, value, false, value === latestPeriod?.slice(4)));
    }

    function pct(value) {
      const n = Number(value || 0) * 100;
      return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
    }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }
    function renderPeriod() {
      const period = `${yearSelect.value}${quarterSelect.value}`;
      const rows = payload.gapStocks.filter(row => row.period === period).sort((a, b) => Number(b.gap_gain) - Number(a.gap_gain));
      if (!periods.has(period)) {
        stockList.innerHTML = `<div class="empty">No data source available for this period.</div>`;
        detail.innerHTML = "";
        return;
      }
      stockList.innerHTML = rows.map((row, index) => `
        <button class="card stock ${index === 0 ? "active" : ""}" data-code="${esc(row.code)}">
          <span><strong>${esc(row.name)}</strong><br><small>${esc(row.code)} · ${esc(row.announcement_date)}</small></span>
          <span class="gain">${pct(row.gap_gain)}</span>
        </button>
      `).join("");
      stockList.querySelectorAll(".stock").forEach(button => {
        button.addEventListener("click", () => {
          stockList.querySelectorAll(".stock").forEach(item => item.classList.remove("active"));
          button.classList.add("active");
          renderCompany(button.dataset.code);
        });
      });
      if (rows[0]) renderCompany(rows[0].code);
    }
    function latestShareholders(code) {
      const rows = payload.shareholders[code] || [];
      const period = rows[0]?.report_period;
      return rows.filter(row => row.report_period === period).slice(0, 10);
    }
    function renderCompany(code) {
      const company = payload.companies[code] || { code, name: code };
      const info = payload.info[code] || {};
      const holders = latestShareholders(code);
      const annual = (payload.annualReports[code] || []).slice(0, 8);
      const research = (payload.researchReports[code] || []).slice(0, 20);
      detail.innerHTML = `
        <section class="panel">
          <h2>${esc(company.name)} ${esc(code)}</h2>
          <div class="info-box">
            <div><span>Main Business</span>${esc(info.main_business || "No cached data.")}</div>
            <div><span>Product Names</span>${esc(info.product_names || "No cached data.")}</div>
            <div><span>Business Scope</span>${esc(info.business_scope || "No cached data.")}</div>
            <div><span>Registered Address</span>${esc(info.registered_address || "No cached data.")}</div>
            <div><span>Office Address</span>${esc(info.office_address || "No cached data.")}</div>
          </div>
        </section>
        <section class="panel">
          <h2>Top Shareholders</h2>
          ${holders.length ? `<table><thead><tr><th>Shareholder</th><th>Holding Ratio</th><th>Change</th></tr></thead><tbody>${holders.map(row => `<tr><td>${esc(row.holder_name)}</td><td>${esc(row.ratio)}%</td><td>${esc(row.change_text)}%</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No cached shareholder data.</div>`}
        </section>
        <section class="panel">
          <h2>Annual Reports</h2>
          <div class="reports">${annual.length ? annual.map(row => `<a class="report" href="${esc(row.url || "#")}" target="_blank" rel="noreferrer"><strong>${esc(row.title)}</strong><span>${esc(row.announcement_date)}</span></a>`).join("") : `<div class="empty">No cached annual reports.</div>`}</div>
        </section>
        <section class="panel">
          <h2>Research Reports</h2>
          <div class="reports">${research.length ? research.map(row => `<a class="report" href="${esc(row.pdf_url || "#")}" target="_blank" rel="noreferrer"><strong>${esc(row.title)}</strong><span>${esc(row.institution)} · ${esc(row.report_date)}</span></a>`).join("") : `<div class="empty">No cached research reports.</div>`}</div>
        </section>
      `;
    }
    document.getElementById("apply").addEventListener("click", renderPeriod);
    renderPeriod();
  </script>
</body>
</html>
"""


def main():
    OUT_DIR.mkdir(exist_ok=True)
    payload = json.dumps(build_payload(), ensure_ascii=False)
    (OUT_DIR / "index.html").write_text(HTML.replace("__PAYLOAD__", payload), encoding="utf-8")
    print(f"Static demo written to {OUT_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
