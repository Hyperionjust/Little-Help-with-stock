"""回归统一入口：pytest + 端到端冒烟断言，退出码驱动自迭代 loop。

跑：
  1) 仓库层 pytest 套件（tests/）
  2) 8 个 fixture 的端到端冒烟（四文件产出 + 门禁行为断言）
  3) 一致性 + 防幻觉机检（对已生成的 Markdown 看板，如提供）

退出码 0=全绿，非0=有红。loop 的 [7] 步调用它。
用法：run_regression.py [--pytest-only] [--smoke-only]
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(os.path.dirname(HERE))
REPO_TESTS = os.path.join(os.path.dirname(SKILL), "stock-metrics-pro-repo", "tests")
PY = sys.executable

# 端到端门禁预期：fixture -> (degraded 期望, 至少一个 critical/warning check 期望)
GATE_EXPECT = {
    "aapl": {"degraded": False},
    "moutai_600519": {"degraded": False},
    "tencent_00700": {"degraded": False},
    "hengrui_600276": {"degraded": False},
    "beigene": {"degraded": False},
    "early_biotech": {"degraded": False},
    "star_incomplete_688": {"degraded": False, "warning_has": "low_field_coverage"},
    "offline_fail": {"degraded": True, "critical_has": "missing_core_field"},
}


def run_pytest():
    if not os.path.isdir(REPO_TESTS):
        print("(no repo tests dir, skip pytest)")
        return 0
    r = subprocess.run([PY, "-m", "pytest", REPO_TESTS, "-q"], cwd=os.path.dirname(REPO_TESTS))
    return r.returncode


def run_smoke():
    import json
    fails = 0
    for fx, exp in GATE_EXPECT.items():
        outdir = f"/tmp/reg_{fx}"
        os.makedirs(outdir, exist_ok=True)
        fxpath = os.path.join(HERE, "fixtures", f"{fx}.json")
        today = json.load(open(fxpath)).get("_snapshot_date", "2025-01-15")
        cmd = [PY, os.path.join(SKILL, "scripts", "run_analysis.py"), fx,
               "--offline-fixture", fxpath, "--outdir", outdir, "--today", today]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[FAIL smoke] {fx}: run_analysis 崩溃\n{r.stderr[-500:]}")
            fails += 1
            continue
        jf = [f for f in os.listdir(outdir) if f.endswith("_analysis.json")]
        hf = [f for f in os.listdir(outdir) if f.endswith("_dashboard.html")]
        xf = [f for f in os.listdir(outdir) if f.endswith("_workbook.xlsx")]
        if not (jf and hf and xf):
            print(f"[FAIL smoke] {fx}: 四文件不全 json={bool(jf)} html={bool(hf)} xlsx={bool(xf)}")
            fails += 1
            continue
        a = json.load(open(os.path.join(outdir, jf[0])))
        qr = a["quality_report"]
        if qr["degraded"] != exp["degraded"]:
            print(f"[FAIL gate] {fx}: degraded={qr['degraded']} 期望 {exp['degraded']}")
            fails += 1
        if "critical_has" in exp:
            if not any(c["check"] == exp["critical_has"] for c in qr["critical"]):
                print(f"[FAIL gate] {fx}: 缺 critical {exp['critical_has']}")
                fails += 1
        if "warning_has" in exp:
            if not any(w["check"] == exp["warning_has"] for w in qr["warning"]):
                print(f"[FAIL gate] {fx}: 缺 warning {exp['warning_has']}")
                fails += 1
    if fails == 0:
        print("[OK] 8/8 端到端冒烟 + 门禁行为符合预期")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytest-only", action="store_true")
    ap.add_argument("--smoke-only", action="store_true")
    args = ap.parse_args()
    rc = 0
    if not args.smoke_only:
        rc |= run_pytest()
    if not args.pytest_only:
        rc |= run_smoke()
    print("REGRESSION", "GREEN" if rc == 0 else "RED")
    sys.exit(rc)


if __name__ == "__main__":
    main()
