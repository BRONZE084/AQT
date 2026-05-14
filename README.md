# AQT: A-Share Quant Toolkit

AQT 是一套面向个人学习和研究的 A 股量化交易软件雏形。当前功能：

- 本地 CSV 数据管理
- A 股常见交易规则模拟：T+1、整百股、停牌、涨跌停、费用
- **每日真实数据拉取**：从公开行情接口获取当日 A 股全量数据
- **实时行情看板**：大盘指数、K 线图、自选股、风险评分、买入建议
- 多策略回测：多因子轮动、均线交叉、通道突破、均值回归
- 回测、交易流水、净值曲线和 Markdown 报告
- 根据最新数据生成次日手动交易计划

它不是自动下单工具，也不构成投资建议。实盘自动交易前，请先向券商确认程序化交易报告、接口权限和合规要求。

## 依赖安装

```powershell
# 核心依赖（数据拉取必需）
python -m pip install akshare

# 可选：提升 EastMoney 接口的稳定性（绕过反爬检测）
python -m pip install curl_cffi
```

其余功能（回测、UI、报告生成）仅需 Python 3.10+ 标准库。

## 快速开始

```powershell
# 方式一：使用样例数据快速体验
python -m aqt init-sample --data-dir data/sample
python -m aqt backtest --data-dir data/sample --out-dir reports/demo --start 2023-07-03 --end 2024-12-31 --cash 1000000
python -m aqt plan --data-dir data/sample --out-dir reports/demo --cash 1000000

# 方式二：拉取真实 A 股每日数据（需安装 akshare）
python -m aqt fetch --data-dir data/live           # 当日增量
python -m aqt fetch --data-dir data/live --init    # 初始化 + CSI 300 历史基准
python -m aqt ui
```

回测完成后查看：

- `reports/demo/report.md`
- `reports/demo/summary.json`
- `reports/demo/equity_curve.csv`
- `reports/demo/trades.csv`
- `reports/demo/trade_plan_YYYYMMDD.csv`

启动 UI 后在浏览器打开 `http://127.0.0.1:8765`。

## Web UI 实时行情看板

启动 `python -m aqt ui` 后，浏览器界面提供以下实时功能：

### 大盘指数条
页面顶部实时显示**上证指数、深证成指、沪深300、创业板指**四大指数价格和涨跌幅，红涨绿跌。盘后显示"已收盘"状态。

### K 线图
输入股票代码（如 `600000`）即可查看交互式 K 线图，包含：
- **日K / 周K / 月K** 三档周期切换（周月线由日线数据聚合生成）
- **K 线**（红涨绿跌）+ **成交量柱**
- **MA5 / MA10 / MA20 / MA60** 四条均线叠加
- **风险评分**：基于波动率、估值、质量的三维评分（低/中/高）
- **买入建议**：综合风险等级和涨跌停状态判断是否适合买入

可通过面板切换按钮在 K 线图和回测净值曲线之间切换。分时图暂不支持（需要分钟级数据源，当前腾讯财经接口仅提供日线快照）。

### 自选股
- 在 K 线图搜索框输入代码后，点击 **+** 加入自选
- 侧边栏实时显示自选股现价、涨跌幅
- 可为每只股票设置**买入价**和**止损价**
- 切换到"自选股"标签查看完整表格（含风险评分和买入建议）
- 点击自选股代码直接加载 K 线图
- 自选股数据持久化保存在 `watchlist.json`

### 实时刷新
盘中（9:30-11:30, 13:00-15:00）每 8 秒自动刷新行情数据，盘后每 30 秒刷新。

## 每日数据拉取

`fetch` 命令从公开行情接口拉取当日 A 股全量数据，生成与样例数据相同格式的 CSV。

```powershell
# 增量拉取今日数据（追加到已有 CSV）
python -m aqt fetch --data-dir data/live

# 首次使用时初始化，同时下载 CSI 300 历史基准
python -m aqt fetch --data-dir data/live --init
```

### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 股票列表 | akshare (`stock_info_a_code_name`) | 非东方财富数据源，获取全量 A 股代码和名称 |
| 行情报价 (OHLCV/PE/PB) | 腾讯财经 (`qt.gtimg.cn`) | GBK 编码，每批 50 只，含涨跌停价自动计算 |
| CSI 300 基准 | 东方财富 (`push2his.eastmoney.com`) | 多后端回退（curl_cffi → curl → requests） |

### 注意事项

- **数据积累**：策略需要至少 20 个交易日的历史数据才能产生有意义的信号。首次拉取后仅有一日数据，交易计划可能为空——持续每日拉取即可。
- **网络环境**：如果系统配置了代理（如 Clash/V2Ray），请确保国内行情域名走直连，否则可能请求失败。东方财富接口对高频请求有 IP 限流。
- **请求速率**：内置 60ms 批次间隔，单次全量拉取约 15 秒，对数据源压力可控。
- **不是爬虫**：本工具访问的是公开 HTTP API（与浏览器访问东方财富/腾讯财经页面无异），使用标准 User-Agent，遵守速率限制，仅用于个人研究。不抓取需要登录的内容，不绕过任何访问控制。

## 数据格式

四类 CSV（`data/sample` 和 `data/live` 结构一致）：

- `universe.csv`：股票基础信息（代码、名称、板块）
- `prices.csv`：日线行情、停牌、ST、涨跌停价
- `fundamentals.csv`：估值指标（PE/PB）
- `benchmark.csv`：CSI 300 基准收盘价

字段定义见 `aqt/data.py` 中的加载逻辑，更详细的字段说明见 `docs/DATA_SCHEMA.md`。

## 策略说明

默认策略是月度调仓的简化多因子策略：

- 动量：最近 60 个交易日收益率，越高越好
- 波动：最近 60 个交易日日收益波动，越低越好
- 估值：PE/PB 越低越好
- 质量：ROE 越高越好

基础过滤：

- 剔除 ST
- 剔除停牌
- 剔除上市时间不足的股票
- 剔除成交额过低的股票
- 买入时避开涨停，卖出时避开跌停

## 下一步建议

1. 增加复权处理和指数成分历史。
2. 接入更多数据源：Tushare Pro、券商导出的成交/持仓 CSV。
3. 加入更严格的未来函数检查。
4. 增加行业中性、单行业上限、黑名单等风控模块。
5. 实盘交易计划自动校验（资金不足、持仓冲突等）。
6. 只在合规确认后，再考虑券商官方接口和自动化执行。

## 测试

```powershell
python -m unittest
```

## 实盘边界

实盘和自动交易注意事项见 `docs/LIVE_TRADING_NOTES.md`。
