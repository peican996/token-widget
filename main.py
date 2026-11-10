# -*- coding: utf-8 -*-
"""入口：初始化存储 → 后台采集线程 → 悬浮窗事件循环"""
import json
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtWidgets import QApplication

import config
import storage
from collectors import build_collectors
from widget import TokenWidget

ERROR_LOG = "%s\\error.log" % config.DATA_DIR


def log_error(source=""):
    """窗口模式没有 stderr，异常统一写文件"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                      source, traceback.format_exc()))
    except OSError:
        pass


def collect_loop(cfg, stop):
    """周期性运行各采集器；UI 通过 QTimer 轮询存储，与采集解耦"""
    collectors = build_collectors(cfg)
    interval = max(10, int(cfg.get("collect_seconds", 15)))
    while not stop.is_set():
        for col in collectors:
            try:
                result = col.collect() or {}
                storage.insert_usage(result.get("usage") or [])
                quota = result.get("quota")
                if quota:
                    storage.insert_quota_snapshot(
                        col.source, datetime.now().isoformat(),
                        quota.get("used_usd"), quota.get("total_usd"),
                        quota.get("label", ""), quota.get("raw", ""),
                    )
                    # 记录当天基线，供“今日”增量计算
                    storage.note_quota_fetch(col.source, quota.get("used_usd"))
                # 入库成功后再提交 offset，失败时下次重读这部分日志
                offsets = result.get("offsets")
                state_key = getattr(col, "state_key", None)
                if offsets is not None and state_key:
                    storage.set_state(state_key, json.dumps(offsets))
            except Exception:
                log_error(getattr(col, "source", ""))
        stop.wait(interval)


def main():
    cfg = config.load_config()
    storage.init_db()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关窗不退出，托盘驻留

    stop = threading.Event()
    t = threading.Thread(target=collect_loop, args=(cfg, stop), daemon=True)
    t.start()

    w = TokenWidget(cfg)
    w.show()

    code = app.exec()
    stop.set()
    sys.exit(code)


if __name__ == "__main__":
    main()
