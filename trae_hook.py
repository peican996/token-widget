# -*- coding: utf-8 -*-
"""给 Trae 主进程注入一个只写的用量钩子（可还原）

用法：
  python trae_hook.py apply     # 注入（自动备份 main.js.bak）
  python trae_hook.py restore   # 还原
  python trae_hook.py status    # 查看状态

注入内容：每次 Trae 取到 entitlement 用量响应后，追加一行到
%APPDATA%\\TokenWidget\\trae_usage.jsonl
"""
import os
import shutil
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Trae CN",
                    "resources", "app", "out", "main.js")
BACKUP = MAIN + ".bak"

ANCHOR = b"this._buildRequestConfig(i));if(!s"
PREFIX = b"this._buildRequestConfig(i));"
HOOK = (b"try{var _fs=(process.getBuiltinModule&&process.getBuiltinModule('fs'));"
        b"if(_fs){_fs.appendFileSync(process.env.APPDATA+'/TokenWidget/trae_usage.jsonl',"
        b"JSON.stringify({at:Date.now(),data:s})+\"\\n\")}}catch(_){}")
MARK = b"trae_usage.jsonl"


def apply():
    if not os.path.exists(MAIN):
        print("未找到 main.js:", MAIN)
        return 1
    data = open(MAIN, "rb").read()
    if MARK in data:
        print("钩子已存在，无需重复注入")
        return 0
    if data.count(ANCHOR) != 1:
        print("锚点数量异常(%d)，为安全起见中止" % data.count(ANCHOR))
        return 1
    patched = data.replace(ANCHOR, PREFIX + HOOK + b"if(!s")

    tmp = os.path.join(tempfile.gettempdir(), "trae_main_patched.js")
    open(tmp, "wb").write(patched)
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print("语法检查无法执行:", e)
        return 1
    if r.returncode != 0:
        print("语法检查失败，已中止：\n", r.stderr[:800])
        return 1
    print("语法检查通过，文件大小 %d -> %d 字节" % (len(data), len(patched)))

    if not os.path.exists(BACKUP):
        shutil.copyfile(MAIN, BACKUP)
        print("已备份到:", BACKUP)
    open(MAIN, "wb").write(patched)
    print("注入完成。重启 Trae 后生效。")
    return 0


def restore():
    if not os.path.exists(BACKUP):
        print("没有备份文件:", BACKUP)
        return 1
    shutil.copyfile(BACKUP, MAIN)
    print("已还原 main.js。重启 Trae 后生效。")
    return 0


def status():
    if not os.path.exists(MAIN):
        print("main.js 不存在")
        return 1
    data = open(MAIN, "rb").read()
    print("main.js:", MAIN)
    print("钩子已注入:", MARK in data)
    print("备份存在:", os.path.exists(BACKUP))
    out = os.path.join(os.environ.get("APPDATA", ""), "TokenWidget", "trae_usage.jsonl")
    print("数据文件存在:", os.path.exists(out),
          os.path.getsize(out) if os.path.exists(out) else "")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"apply": apply, "restore": restore, "status": status}.get(cmd, status)())
