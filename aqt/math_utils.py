from __future__ import annotations

import math


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(variance)


def _rank(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    if len(ordered) == 1:
        return {ordered[0][0]: 1.0}
    result: dict[str, float] = {}
    denominator = len(ordered) - 1
    for idx, (symbol, _value) in enumerate(ordered):
        percentile = idx / denominator
        result[symbol] = percentile if higher_is_better else 1.0 - percentile
    return result


def sma(values: list[float], period: int) -> list[float]:
    if period <= 0 or len(values) < period:
        return []
    result: list[float] = []
    window_sum = sum(values[:period])
    result.append(window_sum / period)
    for idx in range(period, len(values)):
        window_sum += values[idx] - values[idx - period]
        result.append(window_sum / period)
    return result


def ema(values: list[float], period: int) -> list[float]:
    if period <= 0 or len(values) < period:
        return []
    multiplier = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for idx in range(period, len(values)):
        result.append((values[idx] - result[-1]) * multiplier + result[-1])
    return result


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for idx in range(1, period + 1):
        delta = closes[idx] - closes[idx - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for idx in range(period + 1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float]:
    if len(closes) < period:
        return (0.0, 0.0, 0.0)
    window = closes[-period:]
    middle = sum(window) / period
    std = _stdev(window)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return (middle, upper, lower)
