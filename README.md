# A 股收盘行情采集器

这是一个面向定时投资检查的轻量级数据采集项目。它在 A 股收盘后抓取深圳 ETF `159993`的未复权日 K，校验目标交易日，并将结果发布成不含任何私人投资信息的 JSON。

## 数据与校验

- 主数据源：东方财富未复权日 K。
- 独立备用源：腾讯行情日 K。
- 两源同日收盘价一致时标记 `cross_checked`。
- 日期落后时标记 `stale`，价格冲突时标记 `conflict`，当日价格有效但尚无完整历史时标记 `degraded`，不猜测数据。
- 15:05（Asia/Shanghai）以前不把当日行情当作正式收盘。
- 成交量单位是“手”，成交额币种是 CNY。

东方财富映射：`f51` 日期、`f52` 开盘、`f53` 收盘、`f54` 最高、`f55` 最低、`f56` 成交量（手）、`f57` 成交额、`f58` 振幅、`f59` 涨跌幅、`f60` 涨跌额、`f61` 换手率。

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

运行测试：

```bash
pytest -q
```

## 输出文件

```bash
cat public/latest.json
cat public/history/159993.json
```

- `public/latest.json`：目标日收盘价、验证状态、来源状态和生成时间。
- `public/history/159993.json`：最近 90 个交易日（接口实际可用数量为准）。
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
```

如果仓库名恰好是 `<github-user>.github.io`，则 URL 中不需要仓库名路径。

## 验证公网数据不是旧数据

```bash
curl -fsSL https://<github-user>.github.io/<repository>/latest.json
```

检查：

- `status` 必须是 `ok`。
- `market_date` 必须是最近已收盘的 A 股交易日。
- `verification` 优先应为 `cross_checked`。

## 定时时间

Actions 在工作日的北京时间 15:10、15:30、16:00 和 16:20 运行。GitHub 的 cron 可能延迟，因此程序始终使用市场日期校验，不依赖调度时刻判断成功。

## 交易日历维护

仓库内置了 2025–2026 年交易所公布的休市日。到达新年份时程序会明确失败，而不会把未知日期当作交易日。每年交易所发布休市通知后，更新 `market_fetch/trading_calendar.py` 中的 `HOLIDAYS`。

## 限制

东方财富和腾讯接口是无需登录的公开网页接口，但不提供 SLA，字段也可能改变。程序会在结构改变时报错，不会静默生成猜测数据。
