# A 股收盘行情与成交额采集器

这是一个面向定时投资检查的轻量级数据采集项目。它在 A 股收盘后抓取深圳 ETF `159993`的未复权日 K，校验目标交易日，并将结果发布成不含任何私人投资信息的 JSON。

## 数据与校验

- 主数据源：东方财富未复权日 K。
- 独立备用源：腾讯行情日 K。
- 两源同日收盘价一致时标记 `cross_checked`。
- 日期落后时标记 `stale`，价格冲突时标记 `conflict`，当日价格有效但尚无完整历史时标记 `degraded`，不猜测数据。
- 15:05（Asia/Shanghai）以前不把当日行情当作正式收盘。
- 成交量单位是“手”，成交额币种是 CNY。

东方财富映射：`f51` 日期、`f52` 开盘、`f53` 收盘、`f54` 最高、`f55` 最低、`f56` 成交量（手）、`f57` 成交额、`f58` 振幅、`f59` 涨跌幅、`f60` 涨跌额、`f61` 换手率。

## 沪深 A 股成交额口径

`public/a_share_turnover.json` 只统计上海和深圳交易所的 A 股股票成交额，明确不含北交所，也不包含 B 股、基金、债券或股票回购。它不使用上证指数或深证成指的成交额代替全市场数据。

- 上海：上交所官方“每日股票情况”，取 `PRODUCT_CODE=01` 主板 A 股和 `PRODUCT_CODE=03` 科创板的 `TRADE_AMT`。
- 深圳：深交所官方“证券类别统计”，取“主板 A 股”和“创业板 A 股”的 `cjje`。
- 两个官方页面均将成交金额标为“亿元”，程序统一乘以 1 亿，输出整数人民币元。
- 解析时还会校验官方的“股票”合计与 A 股分类 + B 股分类一致（允许页面两位小数带来的最多 0.02 亿元误差），以防字段语义变更后静默产生错误数据。

程序保留最近 30 个正式交易日，自行计算 5/10/20 日均值和阈值判断。任一应有交易日缺失时，`status` 为 `incomplete`，`missing_dates` 列出日期；最近 20 日不完整时不会生成有效的 `avg_20d`。之前已通过验证的公开 JSON 可作为同口径本地缓存回退，但不会用不同口径的沪深京数据补齐。

## 本地运行

```bash
git clone <your-repository-url>
cd <repository-name>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m market_fetch fetch
```

指定期望的交易日：

```bash
python -m market_fetch fetch --date 2026-09-17
```

校验已生成文件：

```bash
python -m market_fetch verify
```

只进行真实网络请求，不改写公开 JSON：

```bash
python -m market_fetch smoke-test
```

抓取并写入沪深 A 股成交额：

```bash
python -m market_fetch turnover
```

只对上交所和深交所官方接口进行真实请求，不写文件：

```bash
python -m market_fetch turnover-smoke-test
```

运行测试：

```bash
pytest -q
```

## 输出文件

```bash
cat public/latest.json
cat public/history/159993.json
cat public/a_share_turnover.json
```

- `public/latest.json`：目标日收盘价、验证状态、来源状态和生成时间。
- `public/history/159993.json`：最近 90 个交易日（接口实际可用数量为准）。
- `public/a_share_turnover.json`：最近 30 个沪深 A 股正式交易日成交额、5/10/20 日均值与阈值判断。
- `data/raw/`：调试用原始响应，已被 `.gitignore` 排除，不会发布。

如果东方财富短暂失败，腾讯仍可验证当日收盘价。程序只会在已有完整历史的基础上追加该日数据；首次运行时若主源失败，`history_status` 会是 `unavailable`，不会用估算成交额伪造历史。

只有 `public/` 会被 GitHub Pages 部署。

## GitHub Pages 部署

1. 在 GitHub 建立一个仓库，将本目录作为仓库根目录推送。
2. 打开仓库的 **Settings → Pages**。
3. 将 **Build and deployment / Source** 设为 **GitHub Actions**。
4. 在 **Settings → Actions → General → Workflow permissions** 中允许 Actions 写入仓库。工作流只会回写 `public/` 中的公开行情 JSON，用于下次运行继承完整历史。
5. 打开 **Actions**，手动运行 **Fetch A-share market data** 工作流。
6. 工作流通过后，在 Pages 页面查看已发布站点地址。

公网 JSON URL 格式是：

```text
https://<github-user>.github.io/<repository>/latest.json
https://<github-user>.github.io/<repository>/history/159993.json
https://<github-user>.github.io/<repository>/a_share_turnover.json?v=YYYYMMDDHHMM
```

如果仓库名恰好是 `<github-user>.github.io`，则 URL 中不需要仓库名路径。

## 验证公网数据不是旧数据

```bash
curl -fsSL https://<github-user>.github.io/<repository>/latest.json
```

读取 Pages JSON 时在 URL 后附加 `?v=YYYYMMDDHHMM` 规避缓存，并检查 JSON 内的 `generated_at` 和 `market_date`。对 159993 还应检查：

- `status` 必须是 `ok`。
- `market_date` 必须是最近已收盘的 A 股交易日。
- `verification` 优先应为 `cross_checked`。

## 本机定时执行

macOS `launchd` 在周一至周五的 16:30 运行本地采集，17:00 再做一次条件重试；如果当日数据已经完整，第二次会直接退出。脚本依次：

1. 确认 Git 工作区干净并快进同步 `origin/main`。
2. 抓取 159993 和沪深 A 股成交额。
3. 只将 `public/` 数据文件提交并通过 SSH 推送 GitHub。
4. GitHub 的 deploy-only workflow 响应 `public/**` push，将 JSON 发布到 Pages；它不在云端重新抓取行情。

安装或重新加载本机任务：

```bash
./scripts/install_launchd.sh
```

安装器会在 `~/market-monitor-runtime` 创建独立的运行副本和 Python 虚拟环境。LaunchAgent 不直接访问 `Documents` 下的开发目录，以避免 macOS 后台进程的隐私权限限制和中文路径兼容问题。

查看状态和日志：

```bash
launchctl print gui/$(id -u)/com.liberalalice.market-monitor
tail -100 ~/Library/Logs/market-monitor.log
tail -100 ~/Library/Logs/market-monitor.error.log
```

本机完全关机时无法执行。GitHub 上的 **Fetch A-share market data** 仍保留 `workflow_dispatch`，可以在需要时手动补跑，但不再使用 GitHub cron 自动抓取。

## 交易日历维护

仓库内置了 2025–2026 年交易所公布的休市日。到达新年份时程序会明确失败，而不会把未知日期当作交易日。每年交易所发布休市通知后，更新 `market_fetch/trading_calendar.py` 中的 `HOLIDAYS`。

## 限制

东方财富和腾讯接口是无需登录的公开网页接口，但不提供 SLA，字段也可能改变。程序会在结构改变时报错，不会静默生成猜测数据。
