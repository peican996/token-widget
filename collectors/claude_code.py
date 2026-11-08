# -*- coding: utf-8 -*-
"""Claude Code 本地日志采集：增量解析 ~/.claude/projects/**/*.jsonl"""
import glob
import json
import os

from pricing import calc_cost_usd
from storage import get_state, set_state

STATE_KEY = "claude_code_offsets"


class ClaudeCodeCollector:
    source = "claude_code"

    def __init__(self, cfg):
        self.log_dir = os.path.expanduser(cfg.get("log_dir", r"~\.claude\projects"))

    def collect(self):
        offsets = self._load_offsets()
        new_offsets = dict(offsets)
        records = []
        pattern = os.path.join(self.log_dir, "**", "*.jsonl")
        for path in glob.glob(pattern, recursive=True):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            offset = offsets.get(path, 0)
            if offset > size:  # 文件被截断/重建，从头读
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
        set_state(STATE_KEY, json.dumps(new_offsets))
        return {"usage": records, "quota": None}

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
        if obj.get("type") != "assistant":
            return None
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        msg_id = msg.get("id")
        ts = obj.get("timestamp")
        if not msg_id or not ts:
            return None
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_write = usage.get("cache_creation_input_tokens") or 0
        if input_tokens + output_tokens + cache_read + cache_write == 0:
            return None
        model = msg.get("model") or ""
        return {
            "source": self.source,
            "record_key": msg_id,  # API 消息 ID 全局唯一，用于幂等去重
            "ts": ts,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "cost_usd": calc_cost_usd(model, input_tokens, output_tokens, cache_read, cache_write),
        }
