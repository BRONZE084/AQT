# Data Schema

把真实 A 股数据接进来时，先保持这些 CSV 文件名和字段不变。

## universe.csv

| field | description |
| --- | --- |
| symbol | 股票代码，如 `600000` |
| name | 股票名称 |
| board | 板块：`main`、`chi_next`、`star` 等 |
| industry | 行业分类，自定义即可 |
| list_date | 上市日期，格式 `YYYY-MM-DD` |

## prices.csv

| field | description |
| --- | --- |
| date | 交易日期 |
| symbol | 股票代码 |
| open/high/low/close | 未复权或后复权价格，但全表口径必须一致 |
| volume | 成交股数 |
| amount | 成交额，单位元 |
| paused | 是否停牌，`1/0` |
| is_st | 是否 ST 或退市风险警示，`1/0` |
| limit_up | 当日涨停价 |
| limit_down | 当日跌停价 |

## fundamentals.csv

| field | description |
| --- | --- |
| date | 指标可用日期。实盘研究中应使用披露后可用日期，不要使用报告期日期替代 |
| symbol | 股票代码 |
| pe_ttm | 滚动市盈率 |
| pb | 市净率 |
| roe_ttm | 滚动 ROE |

## benchmark.csv

| field | description |
| --- | --- |
| date | 交易日期 |
| close | 基准指数收盘点位 |

## 未来函数提醒

- 财务数据应按披露日期或数据供应商的可用日期入库。
- 指数成分股、ST 状态、停复牌、涨跌停价格都要用历史当日状态。
- 复权价格和成交金额要保持口径一致。
- 回测下单日使用上一交易日收盘后可获得的信息，本项目默认按这个规则处理月度调仓。

