# -*- coding: utf-8 -*-
"""WorkBuddy 本地会话日志采集：增量解析 ~/.workbuddy/projects/**/*.jsonl

字段说明（与 Claude Code 不同）：
- message.usage.input_tokens 已包含缓存读取部分，total_tokens = input + output
- 因此入库时把缓存读从 input 中拆出，避免重复累加
"""
import glob
import json
import os
from datetime import datetime, timezone

from pricing import calc_cost_usd
from storage import get_state

STATE_KEY = "workbuddy_offsets"
DEFAULT_DIRS = [r"~\.workbuddy\projects", r"~\.workbuddy-ai\projects"]


class WorkBuddyCollector:
    source = "workbuddy"
    state_key = STATE_KEY

    def __init__(self, cfg):
        dirs = cfg.get("log_dirs") or DEFAULT_DIRS
        self.log_dirs = [os.path.expanduser(d) for d in dirs]

    def collect(self):
        offsets = self._load_offsets()
        new_offsets = dict(offsets)
        records = []
        for d in self.log_dirs:
            for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                offset = offsets.get(path, 0)
                if offset > size:  # 文件被截断/重建
                    offset = 0
                if offset == size:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(offset)
                        chunk = f.read()
                        end = f.tell()
                except OSError:
                    continue
                # 只消费完整的行，避免把正在写入的半行吞掉后永久丢失
                if chunk and not chunk.endswith("\n"):
                    cut = chunk.rfind("\n")
                    if cut == -1:
                        continue
                    chunk = chunk[:cut + 1]
                    end = offset + cut + 1
                new_offsets[path] = end
                for line in chunk.splitlines():
                    rec = self._parse_line(line)
                    if rec:
                        records.append(rec)
        # offset 由调用方在入库成功后提交，避免入库失败时丢数据
        return {"usage": records, "quota": None, "offsets": new_offsets}

    def _load_offsets(self):
        try:
            return json.loads(get_state(STATE_KEY, "{}") or "{}")
        except Exception:
            return {}

    def _parse_line(self, line):
        line = line.strip()
        if not line or '"usage"' not in line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            return None
        key = msg.get("id") or obj.get("id")
        ts = obj.get("timestamp")
        if not key or not ts:
            return None

        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        if input_tokens + output_tokens + cache_read + cache_write == 0:
            return None

        # input_tokens 含缓存读，拆分后避免总量重复计算
        cache_read = min(cache_read, input_tokens)
        input_uncached = input_tokens - cache_read

        pd = obj.get("providerData") or {}
        raw_usage = pd.get("rawUsage") or {}
        model = (pd.get("requestModelName") or pd.get("requestModelId")
                 or msg.get("model") or "")
        credits = raw_usage.get("credit")
        if credits is None:
            credits = pd.get("credit") or 0

        return {
            "source": self.source,
            "record_key": key,
            "ts": self._ts_iso(ts),
            "model": model,
            "input_tokens": input_uncached,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "credits": float(credits or 0),
            "cost_usd": calc_cost_usd(model, input_uncached, output_tokens, cache_read, cache_write),
        }

    @staticmethod
    def _ts_iso(ts):
        t = float(ts)
        if t > 1e11:  # 毫秒时间戳
            ms = int(t) % 1000
            dt = datetime.fromtimestamp(t / 1000.0, tz=timezone.utc)
        else:
            ms = 0
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % ms
