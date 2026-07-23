"""冒烟测试：对一个 fixture 快速跑通四种输出，供单元级 debug 循环 [2] 步使用。

用法：smoke_test.py [fixture_name]   默认 aapl
离线跑（--offline-fixture），产出到 /tmp/smoke_<symbol>/。退出码 0=四文件齐全且门禁行为合理。
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
FX = os.path.join(HERE, "fixtures")
PY = sys.executable

TODAY = {
    "aapl": "2025-01-15", "moutai_600519": "2025-04-15", "tencent_00700": "2025-03-25",
    "hengrui_600276": "2025-04-20", "beigene": "2025-03-10", "early_biotech": "2025-02-20",
    "star_incomplete_688": "2025-05-10", "offline_fail": "2025-06-01",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", nargs="?", default="aapl")
    args = ap.parse_args()
    fx = os.path.join(FX, f"{args.fixture}.json")
    if not os.path.exists(fx):
        print("no such fixture:", fx)
        sys.exit(1)
    outdir = f"/tmp/smoke_{args.fixture}"
    os.makedirs(outdir, exist_ok=True)
    cmd = [PY, os.path.join(SCRIPTS, "run_analysis.py"), args.fixture,
           "--offline-fixture", fx, "--outdir", outdir,
           "--today", TODAY.get(args.fixture, "2025-01-15")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(1)
    # 检查四文件
    import json
    files = os.listdir(outdir)
    j = [f for f in files if f.endswith("_analysis.json")]
    h = [f for f in files if f.endswith("_dashboard.html")]
    x = [f for f in files if f.endswith("_workbook.xlsx")]
    ok = bool(j and h and x)
    print(f"json={bool(j)} html={bool(h)} xlsx={bool(x)}")
    if j:
        a = json.load(open(os.path.join(outdir, j[0])))
        qr = a["quality_report"]
        print(f"gate degraded={qr['degraded']} critical={len(qr['critical'])} warning={len(qr['warning'])}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
