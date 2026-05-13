# AQT: A-Share Quant Toolkit

AQT 是一套面向个人学习和研究的 A 股量化交易软件雏形。第一版专注于：

- 本地 CSV 数据管理
- A 股常见交易规则模拟：T+1、整百股、停牌、涨跌停、费用
- 月度调仓多因子选股策略
- 回测、交易流水、净值曲线和 Markdown 报告
- 根据最新数据生成次日手动交易计划

它不是自动下单工具，也不构成投资建议。实盘自动交易前，请先向券商确认程序化交易报告、接口权限和合规要求。

## 快速开始

当前版本只依赖 Python 标准库，Python 3.10 可直接运行。

```powershell
python -m aqt init-sample --data-dir data/sample
python -m aqt backtest --data-dir data/sample --out-dir reports/demo --start 2023-07-03 --end 2024-12-31 --cash 1000000
python -m aqt plan --data-dir data/sample --out-dir reports/demo --cash 1000000
python -m aqt ui
```

回测完成后查看：

- `reports/demo/report.md`
- `reports/demo/summary.json`
- `reports/demo/equity_curve.csv`
- `reports/demo/trades.csv`
- `reports/demo/trade_plan_YYYYMMDD.csv`

启动 UI 后在浏览器打开 `http://127.0.0.1:8765`。

## 数据格式

`data/sample` 中包含四类 CSV：

- `universe.csv`：股票基础信息
- `prices.csv`：日线行情、停牌、ST、涨跌停价
- `fundamentals.csv`：估值和质量指标
- `benchmark.csv`：基准指数

你后续可以把真实数据源导出的 CSV 替换进去。字段定义见 `aqt/data.py` 中的加载逻辑。
更详细的字段说明见 `docs/DATA_SCHEMA.md`。

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

第一版跑通后，建议按这个顺序扩展：

1. 接入真实数据源：Tushare Pro、AKShare、券商导出的成交/持仓 CSV。
2. 增加复权处理和指数成分历史。
3. 加入更严格的未来函数检查。
4. 增加行业中性、单行业上限、黑名单、最大回撤止损。
5. 只在合规确认后，再考虑券商官方接口和自动化执行。

## 测试

```powershell
python -m unittest
```

## 实盘边界

实盘和自动交易注意事项见 `docs/LIVE_TRADING_NOTES.md`。
