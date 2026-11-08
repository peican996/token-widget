# -*- coding: utf-8 -*-
"""WorkBuddy 积分额度采集：读取本机登录态，调用官方账单汇总接口（只读）

接口：POST https://www.workbuddy.cn/billing/meter/get-user-resource-summary
返回各积分包的总额 / 已用，汇总后写入 quota_snapshots（source=workbuddy_quota）。

登录态为 WorkBuddy 5.6.2+ 的加密信封时，需要客户端运行时提供的密钥；
这部分由用户在配置项 sources.workbuddy_quota.auth_script 指定的本地脚本完成
（该脚本需提供 load_token_best() 和 api_call(host, path, token, payload)）。
未配置或脚本不存在时跳过本采集器，不影响其他来源。
额度变化很慢，默认 3 分钟查询一次，令牌缓存 60 分钟；令牌过期时接口会失败并自动重新读取登录态。
"""
import importlib.util
import os
import time
import traceback

from config import DATA_DIR

SUMMARY_HOST = "https://www.workbuddy.cn"
SUMMARY_PATH = "/billing/meter/get-user-resource-summary"
TOKEN_TTL = 60 * 60
LOG_PATH = os.path.join(DATA_DIR, "workbuddy_quota.log")


def _log(msg):
    """窗口模式下无 stderr，失败原因写入本地日志便于排查"""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _load_auth_module(path):
    spec = importlib.util.spec_from_file_location("wb_checkin_auth", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WorkBuddyQuotaCollector:
    source = "workbuddy_quota"

    def __init__(self, cfg):
        self.interval = max(60, int(cfg.get("interval_seconds", 180)))
        self.auth_script = os.path.expanduser(str(cfg.get("auth_script") or "").strip())
        self._mod = None
        self._mod_tried = False
        self._token = None
        self._token_at = 0.0
        self._last_query = 0.0

    def collect(self):
        now = time.time()
        if now - self._last_query < self.interval:
            return {"usage": [], "quota": None}
        self._last_query = now

        mod = self._auth_module()
        if mod is None:
            _log("未找到可用的 WorkBuddy 登录态模块")
            return {"usage": [], "quota": None}

        token = self._access_token(mod, now)
        if not token:
            _log("未能取得 WorkBuddy accessToken")
            return {"usage": [], "quota": None}

        status, body = mod.api_call(SUMMARY_HOST, SUMMARY_PATH, token, {})
        data = (body or {}).get("data") or {}
        packages = data.get("Packages") or []
        if status != 200 or not packages:
            # 令牌可能过期：下次重新读取登录态
            self._token = None
            _log("额度接口异常 status=%s packages=%d body=%s"
                 % (status, len(packages), str(body)[:200]))
            return {"usage": [], "quota": None}

        total = used = 0.0
        for p in packages:
            total += float(p.get("CycleTotalCapacity") or 0)
            used += float(p.get("CycleUsedCapacity") or 0)
        if total <= 0:
            _log("额度合计为 0，忽略本次结果")
            return {"usage": [], "quota": None}

        remain = data.get("TotalCredits")
        return {"usage": [], "quota": {
            "used_usd": used,
            "total_usd": total,
            "label": "WorkBuddy",
            "raw": '{"remain": %s}' % (remain if remain is not None else "null"),
        }}

    def _auth_module(self):
        if self._mod_tried:
            return self._mod
        self._mod_tried = True
        if not self.auth_script:
            _log("未配置 auth_script，跳过 WorkBuddy 额度采集")
            return None
        if not os.path.isfile(self.auth_script):
            _log("auth_script 不存在：%s" % self.auth_script)
            return None
        try:
            self._mod = _load_auth_module(self.auth_script)
        except Exception:
            _log("加载 auth_script 失败:\n%s" % traceback.format_exc())
            self._mod = None
        return self._mod

    def _access_token(self, mod, now):
        if self._token and now - self._token_at < TOKEN_TTL:
            return self._token
        try:
            token, _domain, _path = mod.load_token_best()
        except Exception:
            _log("读取登录态失败:\n%s" % traceback.format_exc())
            return None
        if not token:
            return None
        self._token = token
        self._token_at = now
        return token
