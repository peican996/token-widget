# -*- coding: utf-8 -*-
"""one-api / new-api 网关额度采集：GET {base}/api/token（Bearer access_token）"""
import json
import urllib.request

QUOTA_PER_USD = 500000  # one-api 约定：500000 quota = 1 美元


class OneApiCollector:
    source = "oneapi"

    def __init__(self, cfg):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.access_token = cfg.get("access_token") or ""
        self.label = cfg.get("label") or "One-API"

    def collect(self):
        if not self.base_url or not self.access_token:
            return {"usage": [], "quota": None}
        try:
            req = urllib.request.Request(
                self.base_url + "/api/token",
                headers={"Authorization": "Bearer " + self.access_token},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"usage": [], "quota": None}
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return {"usage": [], "quota": None}
        used_q = sum((it.get("used_quota") or 0) for it in items if isinstance(it, dict))
        remain_q = sum((it.get("remain_quota") or 0) for it in items if isinstance(it, dict))
        if not used_q and not remain_q:
            return {"usage": [], "quota": None}
        return {"usage": [], "quota": {
            "used_usd": (used_q / QUOTA_PER_USD) if used_q else None,
            "total_usd": (used_q + remain_q) / QUOTA_PER_USD,
            "label": self.label,
            "raw": json.dumps(data, ensure_ascii=False)[:2000],
        }}
