# -*- coding: utf-8 -*-
"""Trae 用量采集：连接 Trae 主进程的调试端口，借助它自己的网络栈请求用量接口

前提：Trae 用 `--inspect=9229` 启动（见 launch-trae-debug.cmd）。
不修改 Trae 任何文件，也不读取明文凭证——请求由 Trae 主进程内部发起，
自带登录态与客户端指纹。
"""
import json
import os
import urllib.request

from config import DATA_DIR
from storage import get_state, set_state

RAW_PATH = os.path.join(DATA_DIR, "trae_usage_raw.json")
HOOK_PATH = os.path.join(DATA_DIR, "trae_usage.jsonl")
STATE_KEY = "trae_api_last"

# 需要在 Trae 主进程里执行的 JS：用 Electron net（等同客户端网络栈）请求用量接口
JS_TEMPLATE = r"""
(async()=>{
  try{
    const _req = (typeof require==='function')
      ? require
      : (await import('node:module')).createRequire(process.execPath);
    const {session,net}=_req('electron');
    const fs=_req('fs');
    const path=_req('path');
    const token=fs.readFileSync(path.join(process.env.USERPROFILE,'.trae-cn','trae-jwt-token'),'utf8').trim();
    const cookies=await session.defaultSession.cookies.get({domain:'.trae.cn'});
    const cookieHeader=cookies.filter(c=>c.name&&c.value).map(c=>c.name+'='+c.value).join('; ');
    return await new Promise((resolve)=>{
      const body=JSON.stringify({require_usage:true,req_source:2});
      const req=net.request({
        method:'POST',
        url:'https://api.trae.cn/trae/api/v2/pay/ide_user_ent_usage',
        useSessionCookies:true,
        credentials:'include'
      });
      req.setHeader('Content-Type','application/json');
      req.setHeader('Authorization','Cloud-IDE-JWT '+token);
      if(cookieHeader){req.setHeader('Cookie',cookieHeader);}
      req.on('response',(res)=>{
        let d='';
        res.on('data',(c)=>{d+=c.toString('utf8');});
        res.on('end',()=>resolve(JSON.stringify({status:res.statusCode,body:d})));
      });
      req.on('error',(e)=>resolve(JSON.stringify({error:String(e&&e.message||e)})));
      req.write(body);
      req.end();
    });
  }catch(e){
    return JSON.stringify({error:String(e&&e.message||e)});
  }
})()
"""

NUM_KEYS = ("used_tokens", "total_tokens", "token_used", "token_total", "used_credit",
            "total_credit", "remain", "remaining", "used", "total", "limit", "usage")


def _find_numbers(obj, hint=()):
    """递归找出形如 used/total 的数值"""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if isinstance(v, (int, float)) and any(h in key for h in NUM_KEYS):
                out["/".join(hint + (str(k),))] = v
            else:
                out.update(_find_numbers(v, hint + (str(k),)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            out.update(_find_numbers(v, hint + ("[%d]" % i,)))
    return out


class TraeApiCollector:
    source = "trae_api"

    def __init__(self, cfg):
        self.port = int(cfg.get("inspector_port", 9229))
        self.label = cfg.get("label") or "Trae"

    def collect(self):
        # 优先读 Trae 主进程钩子写出的数据（trae_hook.py 注入）
        hooked = self._from_hook()
        if hooked:
            return hooked
        result = self._invoke()
        if not result:
            return {"usage": [], "quota": None}
        status = result.get("status")
        body = result.get("body") or ""
        if result.get("error") or status != 200:
            set_state(STATE_KEY, json.dumps({"status": status, "error": result.get("error"),
                                             "at": os.path.getmtime(RAW_PATH) if os.path.exists(RAW_PATH) else None}))
            return {"usage": [], "quota": None}
        try:
            data = json.loads(body)
        except ValueError:
            return {"usage": [], "quota": None}
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            return {"usage": [], "quota": None}

        # 原始响应留档，便于后续按真实字段名调整解析
        try:
            with open(RAW_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

        used, total = self._pick(data)
        if used is None and total is None:
            return {"usage": [], "quota": None}
        return {"usage": [], "quota": {
            "used_usd": used, "total_usd": total, "label": self.label,
            "raw": json.dumps(data, ensure_ascii=False)[:2000],
        }}

    @staticmethod
    def _pick(data):
        # Trae 实际返回：usage_summary.consumed_amount / total_amount（单位：积分）
        us = data.get("usage_summary") or {}
        used = us.get("consumed_amount")
        total = us.get("total_amount")
        if used is not None or total is not None:
            return used, total
        nums = _find_numbers(data)
        if not nums:
            return None, None
        used = total = None
        for path, val in nums.items():
            low = path.lower()
            if used is None and any(w in low for w in ("used", "usage", "consumed", "spent")):
                used = val
            elif total is None and any(w in low for w in ("total", "limit", "quota", "credit")):
                total = val
        if used is None and total is not None:
            # 只有总额时，尝试从 remaining 推算
            for path, val in nums.items():
                if "remain" in path.lower():
                    used = max(0, total - val)
                    break
        return used, total

    def _from_hook(self):
        """读取注入钩子写出的最后一行为用量"""
        if not os.path.exists(HOOK_PATH):
            return None
        try:
            with open(HOOK_PATH, "rb") as f:
                lines = f.readlines()[-3:]
        except OSError:
            return None
        for raw in reversed(lines):
            try:
                obj = json.loads(raw.decode("utf-8", "ignore"))
            except ValueError:
                continue
            data = obj.get("data") if isinstance(obj, dict) else None
            if not isinstance(data, dict):
                continue
            if data.get("code") not in (None, 0):
                continue
            used, total = self._pick(data)
            if used is None and total is None:
                continue
            try:
                with open(RAW_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
            return {"usage": [], "quota": {
                "used_usd": used, "total_usd": total, "label": self.label,
                "raw": json.dumps(data, ensure_ascii=False)[:2000],
            }}
        return None

    def _invoke(self):
        try:
            targets = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/json/list" % self.port, timeout=3).read().decode())
        except Exception:
            return None
        ws_url = None
        for t in targets:
            if t.get("webSocketDebuggerUrl"):
                ws_url = t["webSocketDebuggerUrl"]
                break
        if not ws_url:
            return None
        try:
            from websocket import create_connection
        except ImportError:
            return None
        try:
            ws = create_connection(ws_url, timeout=8)
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": JS_TEMPLATE, "awaitPromise": True,
                           "returnByValue": True, "silent": True},
            }))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    ws.close()
                    val = msg.get("result", {}).get("result", {}).get("value")
                    if isinstance(val, str):
                        return json.loads(val)
                    return None
        except Exception:
            try:
                ws.close()
            except Exception:
                pass
            return None
