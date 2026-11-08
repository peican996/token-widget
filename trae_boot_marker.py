# -*- coding: utf-8 -*-
"""给 main.js 顶部加一次性启动标记，用于确认补丁是否被加载"""
import os
import subprocess
import tempfile

MAIN = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Trae CN",
                    "resources", "app", "out", "main.js")
MARK = b"trae_hook_boot.txt"
BOOT = (b"try{process.getBuiltinModule('fs').appendFileSync("
        b"process.env.APPDATA+'/TokenWidget/trae_hook_boot.txt',"
        b"new Date().toISOString()+\"\\n\")}catch(_){}")

data = open(MAIN, "rb").read()
if MARK in data:
    print("启动标记已存在")
    raise SystemExit(0)

patched = BOOT + data
tmp = os.path.join(tempfile.gettempdir(), "trae_main_boot.js")
open(tmp, "wb").write(patched)
r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60)
if r.returncode != 0:
    print("语法检查失败，已中止：", r.stderr[:600])
    raise SystemExit(1)
open(MAIN, "wb").write(patched)
print("启动标记已写入，文件 %d -> %d 字节" % (len(data), len(patched)))
