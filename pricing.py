# -*- coding: utf-8 -*-
"""模型价格表：按模型名最长前缀匹配，估算费用（美元）。价格单位：美元 / 1M tokens

价格来源优先级：
1. %APPDATA%\\TokenWidget\\prices.json（用户可编辑，首次运行从内置默认复制）
2. 程序内置 prices.json（打包进 exe）
"""
import json
import os
import shutil

from config import DATA_DIR

_PRICES = None


def _user_prices_path():
    return os.path.join(DATA_DIR, "prices.json")


def _bundled_prices_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")


def _load():
    global _PRICES
    if _PRICES is not None:
        return _PRICES

    user_path = _user_prices_path()
    bundled = _bundled_prices_path()
    # 首次运行把内置价格表写到用户目录，方便之后自行修正价格
    if not os.path.exists(user_path) and os.path.exists(bundled):
        try:
            shutil.copyfile(bundled, user_path)
        except OSError:
            pass

    for path in (user_path, bundled):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _PRICES = json.load(f)
                return _PRICES
        except (OSError, ValueError):
            continue
    _PRICES = {}
    return _PRICES


def _match(model):
    if not model:
        return None
    m = model.lower()
    best, best_len = None, -1
    for prefix, price in _load().items():
        if m.startswith(prefix.lower()) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


def calc_cost_usd(model, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0):
    """返回估算费用（美元）；无价格数据时返回 None（只统计 token）"""
    p = _match(model)
    if not p:
        return None
    return (
        input_tokens * p.get("input", 0)
        + output_tokens * p.get("output", 0)
        + cache_read_tokens * p.get("cache_read", 0)
        + cache_write_tokens * p.get("cache_write", 0)
    ) / 1_000_000
