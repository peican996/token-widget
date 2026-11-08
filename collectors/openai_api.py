# -*- coding: utf-8 -*-
"""OpenAI 兼容计费端点采集：仅额度快照（订阅上限 + 本月用量），防御式解析"""
import json
import urllib.request
from datetime import datetime, timedelta


class OpenAIApiCollector:
    source = "openai_api"

    def __init__(self, cfg):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.label = cfg.get("label") or "OpenAI"

    def _get(self, path):
        req = urllib.request.Request(
            self.base_url + path,
            headers={"Authorization": "Bearer " + self.api_key},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def collect(self):
        if not self.base_url or not self.api_key:
            return {"usage": [], "quota": None}
        total, used, raw = None, None, {}
        try:
            sub = self._get("/dashboard/billing/subscription")
            raw["subscription"] = sub
            if isinstance(sub, dict):
                total = sub.get("hard_limit_usd")
        except Exception:
            pass
        try:
            now = datetime.now()
            start = now.replace(day=1).strftime("%Y-%m-%d")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            usage = self._get("/dashboard/billing/usage?start_date=%s&end_date=%s" % (start, end))
            raw["usage"] = usage
            if isinstance(usage, dict) and usage.get("total_usage") is not None:
                used = usage["total_usage"] / 100.0  # 该端点单位为美分
        except Exception:
            pass
        if total is None and used is None:
            return {"usage": [], "quota": None}
        return {"usage": [], "quota": {
            "used_usd": used, "total_usd": total, "label": self.label,
            "raw": json.dumps(raw, ensure_ascii=False)[:2000],
        }}
