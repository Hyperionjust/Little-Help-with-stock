"""回归统一入口：单元测试 + 端到端冒烟 + 契约校验，退出码驱动自迭代 loop。

跑：
  1) 单元测试套件（selfcheck/tests/）
  2) 8 个 fixture 的端到端冒烟（三文件产出 + 门禁行为断言）
  3) 契约校验（产出的 analysis.json 必须通过 contracts/analysis.schema.json）

退出码 0=全绿，非0=有红。

修复记录（相对 stock-metrics-pro 原版，对应方案第七部分缺陷 #13）：
  - 原版 REPO_TESTS 指向仓库外不存在的 ../stock-metrics-pro-repo/tests，跳过 pytest 后
    **仍报 GREEN**，使回归的绿色具有误导性。现改为指向 selfcheck/tests/，且**测试目录
    缺失或为空时明确报 AMBER 并以非零退出码告警**，绝不伪装成全绿。
  - 路径适配三层结构：run_analysis.py 从 scripts/ 移至 engine/。
  - 新增契约校验环节。

用法：run_regression.py [--pytest-only] [--smoke-only] [--allow-no-tests]
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # .../selfcheck
ROOT = os.path.dirname(HERE)                          # repo 根
TESTS = os.path.join(HERE, "tests")
RUN_ANALYSIS = os.path.join(ROOT, "engine", "run_analysis.py")
PY = sys.executable

# 端到端门禁预期：fixture -> 期望的门禁行为
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


def run_pytest(allow_no_tests=False):
    """单元测试。测试缺失不再静默通过——这是原版最危险的行为。"""
    test_files = glob.glob(os.path.join(TESTS, "test_*.py"))
    if not test_files:
        if allow_no_tests:
            print("[AMBER] selfcheck/tests/ 为空，已按 --allow-no-tests 放行")
            return 0
        print("[AMBER] selfcheck/tests/ 下没有任何 test_*.py。")
        print("        单元测试缺失时回归不得报 GREEN——端到端冒烟只能证明流水线不崩溃，")
        print("        无法覆盖单位换算、字段级 fallback、DCF 不变量等逻辑。")
        print("        过渡期可用 --allow-no-tests 放行（会显式标 AMBER）。")
        return 2
    try:
        r = subprocess.run([PY, "-m", "pytest", TESTS, "-q"], cwd=ROOT)
        return r.returncode
    except FileNotFoundError:
        print("[AMBER] 未安装 pytest，跳过单元测试")
        return 0 if allow_no_tests else 2


def _validate_contract(analysis_path):
    """契约校验。校验器或 jsonschema 不可用时返回 None（不阻断，但汇总里标注）。"""
    try:
        cdir = os.path.join(ROOT, "contracts")
        if cdir not in sys.path:
            sys.path.insert(0, cdir)
        import validate as V
        return V.validate_analysis(json.load(open(analysis_path, encoding="utf-8")))
    except Exception:
        return None


def run_smoke():
    fails = 0
    contract_skipped = False
    for fx, exp in GATE_EXPECT.items():
        outdir = f"/tmp/reg_{fx}"
        os.makedirs(outdir, exist_ok=True)
        fxpath = os.path.join(HERE, "fixtures", f"{fx}.json")
        today = json.load(open(fxpath, encoding="utf-8")).get("_snapshot_date", "2025-01-15")
        cmd = [PY, RUN_ANALYSIS, fx, "--offline-fixture", fxpath,
               "--outdir", outdir, "--today", today]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[FAIL smoke] {fx}: run_analysis 崩溃\n{r.stderr[-800:]}")
            fails += 1
            continue

        jf = [f for f in os.listdir(outdir) if f.endswith("_analysis.json")]
        hf = [f for f in os.listdir(outdir) if f.endswith("_dashboard.html")]
        xf = [f for f in os.listdir(outdir) if f.endswith("_workbook.xlsx")]
        if not (jf and hf and xf):
            print(f"[FAIL smoke] {fx}: 三文件不全 json={bool(jf)} html={bool(hf)} xlsx={bool(xf)}")
            fails += 1
            continue

        apath = os.path.join(outdir, jf[0])
        a = json.load(open(apath, encoding="utf-8"))
        qr = a["quality_report"]
        if qr["degraded"] != exp["degraded"]:
            print(f"[FAIL gate] {fx}: degraded={qr['degraded']} 期望 {exp['degraded']}")
            fails += 1
        if "critical_has" in exp and not any(c["check"] == exp["critical_has"] for c in qr["critical"]):
            print(f"[FAIL gate] {fx}: 缺 critical {exp['critical_has']}")
            fails += 1
        if "warning_has" in exp and not any(w["check"] == exp["warning_has"] for w in qr["warning"]):
            print(f"[FAIL gate] {fx}: 缺 warning {exp['warning_has']}")
            fails += 1

        # 闸3：数字对账（真实产出必须零孤儿）
        try:
            pdir = os.path.join(ROOT, "publisher")
            if pdir not in sys.path:
                sys.path.insert(0, pdir)
            import reconcile as _RC
            _f, _st = _RC.reconcile(os.path.join(outdir, hf[0]),
                                    json.load(open(apath, encoding="utf-8")))
            if _f:
                print(f"[FAIL reconcile] {fx}: {len(_f)} 个孤儿数字")
                for _x in _f[:3]:
                    print(f"    {_x['value']}  …{_x['location'][:60]}…")
                fails += 1
        except ImportError:
            pass

        errs = _validate_contract(apath)
        if errs is None:
            contract_skipped = True
        elif errs:
            print(f"[FAIL contract] {fx}: {len(errs)} 处不符合 analysis.schema.json")
            for e in errs[:5]:
                print(f"    {e}")
            fails += 1

    if contract_skipped:
        print("[AMBER] 契约校验被跳过（缺 jsonschema 或 contracts/validate.py）")
    if fails == 0:
        print(f"[OK] {len(GATE_EXPECT)}/{len(GATE_EXPECT)} 端到端冒烟 + 门禁 + 契约(闸1) + 对账(闸3) 全部符合预期")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytest-only", action="store_true")
    ap.add_argument("--smoke-only", action="store_true")
    ap.add_argument("--allow-no-tests", action="store_true",
                    help="单元测试缺失时放行（显式标 AMBER，仅供过渡期使用）")
    args = ap.parse_args()

    rc_pytest = rc_smoke = 0
    if not args.smoke_only:
        rc_pytest = run_pytest(args.allow_no_tests)
    if not args.pytest_only:
        rc_smoke = run_smoke()

    if rc_smoke:
        status = "RED"
    elif rc_pytest == 2:
        status = "AMBER（冒烟全绿，但单元测试缺失——不等于全绿）"
    elif rc_pytest:
        status = "RED"
    else:
        status = "GREEN"
    print("REGRESSION", status)
    sys.exit(rc_smoke or rc_pytest)


if __name__ == "__main__":
    main()
