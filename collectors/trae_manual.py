# -*- coding: utf-8 -*-
"""Trae / WorkBuddy 手动额度：本地无可读用量数据，展示用户在配置中手填的 used/total"""


class TraeManualCollector:
    source = "trae"

    def __init__(self, cfg):
        self.label = cfg.get("label") or "Trae"
        self.used_usd = cfg.get("used_usd")
        self.total_usd = cfg.get("total_usd")

    def collect(self):
        if self.used_usd is None and self.total_usd is None:
            return {"usage": [], "quota": None}
        return {"usage": [], "quota": {
            "used_usd": self.used_usd, "total_usd": self.total_usd,
            "label": self.label, "raw": "manual",
        }}
