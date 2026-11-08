# -*- coding: utf-8 -*-
"""配置加载与保存：%APPDATA%\\TokenWidget\\config.json"""
import json
import os
import sys


def _data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "TokenWidget")
    os.makedirs(d, exist_ok=True)
    return d


DATA_DIR = _data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "sources": {
        "claude_code": {
            "enabled": True,
            "log_dir": os.path.expanduser(r"~\.claude\projects"),
        },
        "workbuddy": {
            "enabled": True,
            "log_dirs": [
                os.path.expanduser(r"~\.workbuddy\projects"),
                os.path.expanduser(r"~\.workbuddy-ai\projects"),
            ],
        },
        # 额度（总额/已用/剩余）从 WorkBuddy 官方账单接口只读采集
        "workbuddy_quota": {
            "enabled": True,
            "interval_seconds": 180,
            # 可选：提供 load_token_best() 与 api_call() 的本地脚本路径，留空则跳过采集
            "auth_script": "",
        },
        "openai_api": {
            "enabled": False,
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "label": "OpenAI",
        },
        "oneapi": {
            "enabled": False,
            "base_url": "http://localhost:3000",
            "access_token": "",
            "label": "One-API",
        },
        "trae_api": {
            "enabled": True,
            "inspector_port": 9229,
            "label": "Trae",
        },
        "trae": {
            "enabled": False,
            "label": "Trae",
            "used_usd": None,
            "total_usd": None,
        },
    },
    "ui": {
        "opacity": 0.8,
        "always_on_top": True,
        "autostart": False,
        "refresh_seconds": 15,
        "ball_mode": False,
        "pos": None,
    },
    "collect_seconds": 15,
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        _deep_update(cfg, user)
    except FileNotFoundError:
        save_config(cfg)
    except Exception:
        pass
    return cfg


def _deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def set_autostart(enabled):
    """Windows 开机自启：写 HKCU\\...\\CurrentVersion\\Run"""
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if getattr(sys, "frozen", False):
        # 打包为 exe 后，sys.executable 即程序本体
        cmd = '"%s"' % sys.executable
    else:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        cmd = '"%s" "%s"' % (sys.executable, script)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "TokenWidget", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "TokenWidget")
            except FileNotFoundError:
                pass
