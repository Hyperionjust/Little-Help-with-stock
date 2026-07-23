"""结构化诊断对象 + 运行清单（方案 §12.2-12.3）。

每一层的每次失败都产出同一形状的诊断对象：
  layer/stage/code/severity + 双语 message + evidence + agent_action + user_action

三个字段是设计核心：
  agent_action —— 约束 LLM 的重试行为（该重试还是换源、该降级还是停）。
                  防止"看到报错就无脑重试"和"静默跳过"。
  user_action  —— 告诉用户他能做什么，把死路变成岔路。
                  引导层在失败路径上的内容直接取自这里。
  evidence     —— 让"它坏了"变成可远程诊断。朋友分发场景的刚需：
                  用户把 run_manifest.json 发回来，不需要复现环境就能定位。

错误码必须已在 references/error-codes.json 登记——禁止散落裸字符串。
"""
from __future__ import annotations
import hashlib
import json
import os
import platform
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402
from _util import now_iso  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODES_PATH = os.path.join(os.path.dirname(_HERE), "references", "error-codes.json")

_codes_cache = None


def _registry():
    global _codes_cache
    if _codes_cache is None:
        try:
            with open(_CODES_PATH, encoding="utf-8") as f:
                _codes_cache = json.load(f)["codes"]
        except (OSError, KeyError, json.JSONDecodeError):
            _codes_cache = {}
    return _codes_cache


def diag(code, stage, evidence=None, fill=None, layer=None, severity=None):
    """构造一条诊断。code 必须已登记；fill 用于填充 user_action 里的 {占位符}。

    未登记的 code 不抛错（诊断系统自己不能崩），但会在对象里标 _unregistered，
    回归测试会抓这个标记。
    """
    reg = _registry().get(code)
    d = {
        "layer": layer or (reg or {}).get("layer", "engine"),
        "stage": stage,
        "code": code,
        "severity": severity or (reg or {}).get("severity", "warning"),
        "retriable": (reg or {}).get("retriable", False),
        "ts": now_iso(),
    }
    if reg:
        d["agent_action"] = reg.get("agent_action")
        ua_zh, ua_en = reg.get("user_action_zh", ""), reg.get("user_action_en", "")
        if fill:
            for k, v in fill.items():
                token = "{%s}" % k
                ua_zh, ua_en = ua_zh.replace(token, str(v)), ua_en.replace(token, str(v))
        d["user_action_zh"], d["user_action_en"] = ua_zh, ua_en
    else:
        d["_unregistered"] = True
    if evidence:
        d["evidence"] = evidence
    return d


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return None


def _adapters_available():
    """环境指纹的一部分：哪些可选数据源当前可用。"""
    out = {}
    for mod in ["akshare", "efinance", "baostock", "yfinance", "tushare"]:
        try:
            __import__(mod)
            out[mod] = True
        except ImportError:
            out[mod] = False
    out["tushare_token"] = bool(os.environ.get("TUSHARE_TOKEN"))
    return out


class RunManifest:
    """运行清单：一次运行的完整可复现记录，与产物同目录落盘。

    用途：朋友说"它出来的数字不对/它报错了"，把这个文件发回来即可定位，
    不需要复现环境。同时记录三份产物的哈希，可验证 analysis/model/报告
    是否同一次运行的产物——防止拿旧数据配新报告。
    """

    def __init__(self, query, outdir):
        self._t0 = time.time()
        self.data = {
            "manifest_version": "1.0",
            "started_at": now_iso(),
            "query": query,
            "outdir": os.path.abspath(outdir),
            "env": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "engine_schema_version": None,   # run_analysis 填
                "adapters": _adapters_available(),
            },
            "resolution": None,
            "chain": {"field_sources": None, "data_gaps": None,
                      "financials_source": None, "mixed_source_periods": None},
            "gates": {},
            "diagnostics": [],
            "stages_ms": {},
            "artifacts": {},
        }

    def stage_done(self, name):
        self.data["stages_ms"][name] = round((time.time() - self._t0) * 1000)

    def add_diag(self, d):
        self.data["diagnostics"].append(d)

    def record_artifact(self, kind, path):
        self.data["artifacts"][kind] = {"path": os.path.basename(path),
                                        "hash": _sha256(path)}

    def write(self):
        self.data["finished_at"] = now_iso()
        path = os.path.join(self.data["outdir"], "run_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        return path
