# -*- coding: utf-8 -*-
"""可插拔采集器注册表"""
from .claude_code import ClaudeCodeCollector
from .openai_api import OpenAIApiCollector
from .oneapi import OneApiCollector
from .trae_api import TraeApiCollector
from .trae_manual import TraeManualCollector
from .workbuddy import WorkBuddyCollector
from .workbuddy_quota import WorkBuddyQuotaCollector

COLLECTORS = {
    "claude_code": ClaudeCodeCollector,
    "workbuddy": WorkBuddyCollector,
    "workbuddy_quota": WorkBuddyQuotaCollector,
    "trae_api": TraeApiCollector,
    "openai_api": OpenAIApiCollector,
    "oneapi": OneApiCollector,
    "trae": TraeManualCollector,
}


def build_collectors(cfg):
    """按配置启用项实例化采集器"""
    out = []
    for name, cls in COLLECTORS.items():
        scfg = cfg.get("sources", {}).get(name, {})
        if scfg.get("enabled"):
            out.append(cls(scfg))
    return out
