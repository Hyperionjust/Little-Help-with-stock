"""契约执法：闸1（schema 校验）与闸2（模型自洽）。

为什么必须有这个文件：
stock-metrics-pro 的 analysis_schema.json 写得不错（285 行、12 个必填顶层键），但
**全 repo 没有一次校验调用，requirements 里也没有 jsonschema**——它是文档，不是契约。
契约的价值在于会执行。本模块就是那个执行者。

三道闸（方案 §3.4）：
  闸1 schema 校验   —— 本模块 validate_analysis / validate_model
  闸2 模型自洽      —— 本模块 check_model_invariants（不变量 INV-1..17）
  闸3 数字对账      —— publisher/reconcile.py（阶段1）

用法：
  python validate.py analysis <path>      # 校验 analysis.json
  python validate.py model <path>         # 校验 model.json + 跑不变量
  退出码 0=通过，1=不通过，2=校验器不可用（缺 jsonschema）
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_SCHEMA = os.path.join(HERE, "analysis.schema.json")
MODEL_SCHEMA = os.path.join(HERE, "model.schema.json")

# 浮点容差：会计恒等式要求严格，估值口径允许更松
TOL_ACCOUNTING = 1e-6
TOL_VALUATION = 1e-4


class ContractUnavailable(RuntimeError):
    """jsonschema 未安装。调用方应降级为 AMBER 而非静默通过。"""


def _load_schema(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate(instance, schema_path):
    """返回错误字符串列表。空列表 = 通过。"""
    try:
        import jsonschema
    except ImportError as e:
        raise ContractUnavailable(
            "未安装 jsonschema，契约校验无法执行。pip install jsonschema"
        ) from e

    schema = _load_schema(schema_path)
    validator = jsonschema.Draft7Validator(schema)
    errs = []
    for e in sorted(validator.iter_errors(instance), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errs.append(f"{loc}: {e.message}")
    return errs


def validate_analysis(instance):
    """闸1 —— engine 产出后、analyst 消费前。"""
    return _validate(instance, ANALYSIS_SCHEMA)


def validate_model(instance):
    """闸1 —— analyst 产出后、publisher 消费前（仅结构）。"""
    return _validate(instance, MODEL_SCHEMA)


# ────────────────────────────────────────────────────────────────────
# 闸2：模型自洽不变量（方案 §8.2 INV-1..17）
# 这些不是 schema 能表达的——schema 管形状，不变量管数值关系。
# ────────────────────────────────────────────────────────────────────

def check_model_invariants(m):
    """返回 [(inv_id, passed, detail)]。任一 critical 不通过即阻断进入出版层。"""
    out = []

    def rec(inv, ok, detail=""):
        out.append((inv, bool(ok), detail))

    proj = m.get("projections") or {}
    checks = proj.get("checks") or {}
    dcf = m.get("dcf") or {}
    sens = m.get("sensitivity") or {}
    scen = m.get("scenarios") or {}
    level = m.get("valuation_level")

    # INV-1/2/3 三表勾稽（L2 才有意义）
    if level == "L2":
        for inv, key in [("INV-1 资产负债表平衡", "balance_sheet_balanced"),
                         ("INV-2 现金勾稽", "cash_tie_out"),
                         ("INV-3 历史回算误差<1%", "historical_replication")]:
            c = checks.get(key) or {}
            rec(inv, c.get("passed") is True,
                f"value={c.get('value')} tol={c.get('tolerance')}")
    else:
        rec("INV-1..3 三表勾稽", True, "L1 模式跳过（无三表预测）")

    # INV-4 分部合计 == 总收入
    c = checks.get("revenue_ties_to_segments")
    if c is not None:
        rec("INV-4 分部合计=总收入(±2%)", c.get("passed") is True, f"value={c.get('value')}")

    # INV-5 敏感性中心格 == DCF 基准
    if sens:
        rec("INV-5 敏感性中心格=DCF基准", sens.get("base_cell_equals_dcf") is True)
        rec("INV-6 全矩阵 g<WACC", sens.get("g_lt_wacc_all_cells") is True)

    # INV-7 TV/EV < 0.80：超限本身不阻断，但**必须有说明性 warning**——
    # 未经说明的高终值占比是隐瞒，有说明的高终值占比是信息。
    term = dcf.get("terminal") or {}
    tv_pct = term.get("tv_pct_of_ev")
    if tv_pct is not None:
        explained = any(w.get("check") == "tv_dominates_ev"
                        for w in (m.get("gate") or {}).get("warning", []))
        rec("INV-7 TV占EV<0.80或已显式说明", tv_pct < 0.80 or explained,
            f"tv_pct_of_ev={tv_pct:.3f}" + ("（已附说明性警告）" if explained else ""))

    # DCF 方法学红线（本产品相对原素材的四项修正）
    if dcf:
        rec("DCF-a 启用期中折现", dcf.get("mid_year_convention") is True)
        bridge = dcf.get("equity_bridge") or {}
        rec("DCF-b 权益桥不重复计现金",
            "add_excess_cash" not in bridge,
            "净负债已含全部现金，不得再加回超额现金")
        rec("DCF-c 股本与SBC联动", dcf.get("sbc_linked_to_shares") is not False)
        w = dcf.get("wacc") or {}
        g = term.get("g")
        if g is not None and w.get("wacc") is not None:
            rec("DCF-d 永续增长 < WACC", g < w["wacc"], f"g={g} wacc={w['wacc']}")

    # INV-8 情景概率
    if scen:
        ps = scen.get("probability_sum")
        rec("INV-8a 概率和=1.00",
            ps is not None and abs(ps - 1.0) < TOL_VALUATION, f"sum={ps}")
        if scen.get("base_probability_in_band") is not None:
            rec("INV-8b 基准情景概率在45-60%", scen["base_probability_in_band"] is True)

    # 历史估值带统计量必须脚本算出
    hb = m.get("historical_band")
    if hb:
        rec("统计量由脚本计算", hb.get("computed_from_series") is True,
            "mean/std/percentile 不接受调用方直接喂入")

    # INV-13..15 来自 xtt model-audit-tieout
    for inv, key in [("INV-13 无硬编码覆盖公式", "no_hardcode_override"),
                     ("INV-14 符号/单位约定一致", "sign_unit_conventions")]:
        c = checks.get(key)
        if c is not None:
            rec(inv, c.get("passed") is True)

    kfs = m.get("key_figures") or []
    if kfs:
        bad = [k["id"] for k in kfs
               if k.get("tie_out_status") == "untied"
               or (k.get("tie_out_status") == "variance" and not k.get("variance_explanation"))]
        rec("INV-15 关键数字均已 tie 或有偏差说明", not bad, f"未达标: {bad[:5]}")

    # INV-17 发布阻断
    gate = m.get("gate") or {}
    rec("INV-17 无未关闭的发布阻断项", not gate.get("release_blocker"))

    # 假设诚实性：user_assumption 必须有 basis
    unbased = [a["id"] for a in m.get("assumptions", [])
               if a.get("source_type") == "user_assumption" and not a.get("basis")]
    rec("主观假设均有取值依据", not unbased, f"缺依据: {unbased[:5]}")

    return out


def gate2(m):
    """闸2 综合判定。返回 (passed, failures)。"""
    results = check_model_invariants(m)
    failures = [(i, d) for i, ok, d in results if not ok]
    return (not failures), failures


# ────────────────────────────────────────────────────────────────────

def _cli():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    kind, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as f:
        inst = json.load(f)

    try:
        errs = validate_analysis(inst) if kind == "analysis" else validate_model(inst)
    except ContractUnavailable as e:
        print(f"[AMBER] {e}")
        return 2

    if errs:
        print(f"[FAIL] {path} 有 {len(errs)} 处不符合契约：")
        for e in errs[:30]:
            print(f"  {e}")
        if len(errs) > 30:
            print(f"  ...另有 {len(errs)-30} 处")
        return 1
    print(f"[OK] {path} 通过 schema 校验")

    if kind == "model":
        passed, failures = gate2(inst)
        if not passed:
            print(f"[FAIL] 闸2 模型自洽：{len(failures)} 项不变量未通过")
            for inv, detail in failures:
                print(f"  {inv} {detail}")
            return 1
        print("[OK] 闸2 全部不变量通过")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
