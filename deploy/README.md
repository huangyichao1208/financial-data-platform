# 成交额采集与定时运行

Flask 首页新增「情绪指标」，包含成交量（每日成交额表格）与炸板率（每日涨停股数、炸板股数与炸板率）。此实现针对 Flask 站点，export_static.py 的旧静态快照不包含新页面。

采集口径沿用提供的 AKShare 东方财富日线接口 stock_zh_index_daily_em 的上证指数 sh000001、深证成指 sz399001 的 amount 字段；数据库单位元，网页单位亿元。这是指定指数的成交额口径，不是成交股数，也不包含北交所。

## Linux 服务器安装

将修改后的 app.py、market_amount.py、market_break_rate.py、collect_sentiment.py、templates、static、requirements.txt、deploy 上传到 /opt/finance-app，保留服务器原有 finance_guide.sqlite3，不要用本地数据库覆盖。

以下命令在服务器执行。已有虚拟环境可复用，将 service 的 ExecStart 改为实际 Python 路径。建议在 service 的 [Service] 中设置 User 为现有应用运行用户，并保证它可写数据库及其目录。

```sh
cd /opt/finance-app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 首次补齐历史，可自行改变起始日期
.venv/bin/python market_amount.py --start-date 2026-01-01
sudo cp deploy/market-amount.service deploy/market-amount.timer /etc/systemd/system/
sudo systemctl daemon-reload
systemd-analyze calendar '*-*-* 10:00:00 America/Vancouver'
sudo systemctl enable --now market-amount.timer
systemctl list-timers market-amount.timer
```

更新后重启现有 Flask 应用服务，即可访问 /sentiment。定时器明确使用 America/Vancouver，不依赖服务器所在时区；请保持服务器 tzdata 最新，使时间规则与温哥华一致。Persistent=true 会在服务器停机错过运行时于恢复后补跑。

默认结束日期为中国昨天（温哥华早上 10 点时，中国已进入次日）。默认重拉最近 7 天；若已有记录更早则从最后记录日起补齐。重复运行会更新已有日期，不产生重复行。两边均无记录则跳过；接口失败、金额无效、沪深日期不一致则报错，不写入半份数据。无历史记录时默认仅拉最近一周，完整历史使用 --start-date。指定区间可同时使用 --start-date 和 --end-date（YYYY-MM-DD）。

```sh
# 手动触发与查看日志
sudo systemctl start market-amount.service
journalctl -u market-amount.service -n 100 --no-pager
# 停用
sudo systemctl disable --now market-amount.timer
```

配置文件已准备，但不会通过本地编辑自动安装到远端服务器。

参考：[systemd 时间与时区语法](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html)、[AKShare 指数接口](https://akshare.akfamily.xyz/data/index/index.html)。

## 运行时间与行情日期

例如温哥华 2026-09-08 10:00，对应中国 2026-09-09 01:00。脚本先转换到 Asia/Shanghai，再取日期减一天，因此查询 2026-09-08 的日线成交额（该交易日收盘后的全日数据）。不向日线接口传入 15:00；frequency="d" 的 start_date/end_date 使用 YYYY-MM-DD。若目标日期是休市日，不生成该日零值记录。

BC 自 2026 年 3 月起采用全年 UTC-7，请保持服务器 tzdata 最新。来源：https://news.gov.bc.ca/releases/2026AG0013-000209

如果之前已经安装了 9 点的定时器，上传新版配置后执行：

```sh
sudo cp /opt/finance-app/deploy/market-amount.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart market-amount.timer
systemctl list-timers market-amount.timer
```

## 炸板率接入

新增 market_break_rate.py 与 collect_sentiment.py，部署时也需要上传。现有 market-amount.timer 仍每天温哥华 10 点触发，service 改为运行 collect_sentiment.py，依次运行成交额和炸板率采集。每项最多 240 秒；一项失败仍执行另一项，并将整体任务标记失败供日志检查。

炸板率 = 炸板股数 / (涨停股数 + 炸板股数)，按股票行数统计，数据库存储小数，网页显示百分比。口径来自东方财富两个股池，不代表全市场所有股票；AKShare 源码说明其专题有 ST、科创板等范围限制。两接口使用同一个中国昨天日期并转换为 YYYYMMDD。炸板率默认只采集该日，不自动追溯历史；历史是否可用取决于上游。

AKShare 空表可能表示股池为零，也可能数据缺失：任一股池为空均跳过且记录日志，不写成 0% 或 100%。因此真实零股数日期也暂不入库。接口异常、字段异常均不会覆盖已有数据。

```sh
cd /opt/finance-app
.venv/bin/pip install -r requirements.txt
# 单独验证炸板率，或 --date 2026-09-07 指定中国日期
.venv/bin/python market_break_rate.py
# 同时运行两个采集器
.venv/bin/python collect_sentiment.py
sudo cp deploy/market-amount.service deploy/market-amount.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-amount.timer
sudo systemctl restart market-amount.timer
```

重启现有 Flask 网站服务以加载新路由和表结构。不要覆盖服务器数据库。
参考：https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_ztb_em.py

## 成交额切换到 AKShare

market_amount.py 已去除 BaoStock 登录及依赖，改用 stock_zh_index_daily_em，保留数据库结构、日期参数和金额单位。已有完整部署只需更新 market_amount.py 与 requirements.txt；collect_sentiment.py、网页和定时器无需修改或重新安装。更新脚本后运行 `.venv/bin/python market_amount.py` 验证。首次切换仍按原规则刷新最近一周，可能按新数据源修正已有数值；更早历史需显式 --start-date 才会刷新。
