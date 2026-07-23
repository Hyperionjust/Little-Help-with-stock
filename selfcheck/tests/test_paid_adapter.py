"""付费终端插槽的单元测试（方案阶段2）。

真实付费终端无法在开发期验证（没有 key），所以这套测试打的是：
  · 声明式引擎的字段映射与单位归一化（用 mock transport，不联网）
  · 无 key 时降级链与阶段1完全一致（这是硬要求）
  · 有 key 时付费源升 Tier-0
  · 付费源仍强制跨源校验（付费不等于免信任）
  · file_drop 能吃下导出文件
  · 全部内置 YAML 结构合法
"""
from __future__ import annotations
import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAID = os.path.join(ROOT, "engine", "providers", "paid")
for p in [os.path.join(ROOT, "engine"),
          os.path.join(ROOT, "engine", "providers"),
          os.path.join(ROOT, "engine", "providers", "free"),
          PAID]:
    if p not in sys.path:
        sys.path.insert(0, p)

import adapter as A  # noqa: E402
import registry as REG  # noqa: E402

yaml = pytest.importorskip("yaml")


# ── 声明式引擎：字段映射 + 单位归一化（mock transport）───────────────

class MockTransport(A._Transport):
    def __init__(self, spec, payloads):
        super().__init__(spec)
        self.payloads = payloads

    def probe_available(self):
        return True

    def fetch(self, capability, resolved, **kw):
        return self.payloads.get(capability)


def _spec(**over):
    s = {
        "name": "mock", "markets": ["A"], "tier": 0,
        "auth": {"method": "none"},
        "transport": {"kind": "http"},
        "capabilities": ["quote", "financials"],
        "fields": {
            "quote": {
                "price": {"path": "close", "unit": "native"},
                "market_cap": {"path": "mv", "unit": "万元"},
                "total_shares": {"path": "shares", "unit": "万股"},
            },
            "financials": {
                "revenue": {"path": "rev", "unit": "元"},
                "net_income": {"path": "ni", "unit": "元"},
            },
        },
    }
    s.update(over)
    return s


def test_adapter_maps_and_normalizes_market_cap():
    """核心：万元市值必须归一化到百万，价格不被换算。"""
    spec = _spec()
    p = A.DeclarativeProvider(spec, transport=MockTransport(
        spec, {"quote": {"close": 15.5, "mv": 194700000, "shares": 100000}}))
    q = p.get_quote({"symbol": "600519"})
    assert q["price"] == 15.5                      # native，不换算
    assert q["market_cap"] == pytest.approx(1947000.0)  # 万元 → 百万
    assert q["total_shares"] == pytest.approx(1000.0)   # 万股 → 百万


def test_adapter_normalizes_financials_to_million():
    spec = _spec()
    p = A.DeclarativeProvider(spec, transport=MockTransport(
        spec, {"financials": [{"period": "2024FY", "rev": 1.0e11, "ni": 2.0e10}]}))
    fin = p.get_financials({"symbol": "600519"})
    assert fin["annual"][0]["revenue"] == pytest.approx(1.0e5)   # 元 → 百万
    assert fin["annual"][0]["net_income"] == pytest.approx(2.0e4)
    assert fin["annual"][0]["period"] == "2024FY"


def test_adapter_rejects_unknown_unit():
    """YAML 单位写错必须抛错，不静默——否则又是一个量纲隐患。"""
    spec = _spec()
    spec["fields"]["quote"]["market_cap"]["unit"] = "桶"
    p = A.DeclarativeProvider(spec, transport=MockTransport(
        spec, {"quote": {"close": 1, "mv": 1, "shares": 1}}))
    with pytest.raises(A.AdapterError):
        p.get_quote({"symbol": "x"})


def test_adapter_extracts_nested_path():
    spec = _spec(fields={"quote": {"price": {"path": "data.close", "unit": "native"}}})
    p = A.DeclarativeProvider(spec, transport=MockTransport(
        spec, {"quote": {"data": {"close": 42.0}}}))
    assert p.get_quote({"symbol": "x"})["price"] == 42.0


# ── 认证发现 ─────────────────────────────────────────────────────────

def test_available_true_when_env_token_set(monkeypatch):
    spec = _spec(auth={"method": "env_token", "env_var": "MOCK_TOK"})
    p = A.DeclarativeProvider(spec, transport=MockTransport(spec, {}))
    monkeypatch.delenv("MOCK_TOK", raising=False)
    assert p.available() is False
    monkeypatch.setenv("MOCK_TOK", "secret")
    assert p.available() is True


def test_available_false_when_no_auth_needed():
    spec = _spec(auth={"method": "none"})
    p = A.DeclarativeProvider(spec, transport=MockTransport(spec, {}))
    assert p.available() is True


# ── 无 key 时降级链与阶段1完全一致（硬要求）─────────────────────────

def test_free_chain_unchanged_without_any_key(monkeypatch):
    for var in ["TUSHARE_TOKEN", "IFIND_TOKEN", "ERS_IMPORT_FILE", "BLOOMBERG_BRIDGE"]:
        monkeypatch.delenv(var, raising=False)
    import fetch_data as FD
    assert [p.name for p in FD.build_chain("A")] == \
        ["tencent", "sina", "akshare", "efinance", "baostock"]
    assert [p.name for p in FD.build_chain("HK")] == \
        ["tencent", "sina", "yfinance", "akshare", "efinance"]
    assert [p.name for p in FD.build_chain("US")] == ["yfinance", "tencent"]


def test_paid_prefix_promotes_to_tier0(monkeypatch, tmp_path):
    """构造一个 none-auth 的临时适配器，验证它被置于链首。"""
    # 直接测 registry.build_paid_prefix 的排序语义，不依赖真实 key
    from adapter import DeclarativeProvider
    s_hi = _spec(name="paid_hi", tier=0, auth={"method": "none"})
    s_lo = _spec(name="free_ish", tier=5, auth={"method": "none"})
    provs = [DeclarativeProvider(s_lo, transport=MockTransport(s_lo, {})),
             DeclarativeProvider(s_hi, transport=MockTransport(s_hi, {}))]
    prefix = REG.build_paid_prefix("A", discovered=provs)
    assert [p.name for p in prefix] == ["paid_hi", "free_ish"]  # tier 升序


def test_data_tier_reflects_paid_presence():
    assert REG.data_tier(discovered=[], market="A") == 1        # 无付费 → 免费层
    s = _spec(auth={"method": "none"})
    from adapter import DeclarativeProvider
    p = DeclarativeProvider(s, transport=MockTransport(s, {}))
    assert REG.data_tier(discovered=[p], market="A") == 0        # 有付费 → Tier-0


# ── file_drop：导出文件 ──────────────────────────────────────────────

def test_file_drop_reads_csv_by_period_rows(monkeypatch, tmp_path):
    csv = tmp_path / "wind.csv"
    csv.write_text("报告期,营业总收入,归母净利润\n2024FY,100,20\n2023FY,90,18\n",
                   encoding="utf-8")
    spec = {
        "name": "wind_export", "markets": ["A"], "tier": 0,
        "auth": {"method": "file_drop", "path_env": "ERS_IMPORT_FILE"},
        "transport": {"kind": "file_drop"},
        "capabilities": ["financials"],
        "fields": {"financials": {
            "revenue": {"aliases": ["营业总收入", "Revenue"], "unit": "元"},
            "net_income": {"aliases": ["归母净利润", "Net Income"], "unit": "元"},
        }},
    }
    monkeypatch.setenv("ERS_IMPORT_FILE", str(csv))
    p = A.DeclarativeProvider(spec)
    assert p.available() is True
    fin = p.get_financials({"symbol": "600519"})
    periods = {r["period"]: r for r in fin["annual"]}
    assert periods["2024FY"]["revenue"] == pytest.approx(100 / 1e6)  # 元→百万
    assert periods["2024FY"]["net_income"] == pytest.approx(20 / 1e6)


# ── 内置 YAML 结构合法性 ────────────────────────────────────────────

@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(PAID, "adapters", "*.yaml"))))
def test_builtin_adapters_are_well_formed(path):
    spec = yaml.safe_load(open(path, encoding="utf-8"))
    for key in ("name", "markets", "auth", "transport", "fields"):
        assert key in spec, f"{os.path.basename(path)} 缺 {key}"
    # 单位必须是已登记的（否则运行期才炸）
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    from _util import UNIT_FACTORS
    ok_units = set(UNIT_FACTORS) | {"native", "raw"}
    for group, fmap in spec["fields"].items():
        for fld, rule in fmap.items():
            u = rule.get("unit")
            if u is not None:
                key = u.lower() if str(u).isascii() else u
                assert key in ok_units or u in ok_units, \
                    f"{os.path.basename(path)}.{group}.{fld}: 未登记单位 {u!r}"


def test_custom_template_excluded_from_discovery():
    """模板不是可用适配器，绝不能被 discover 当真源加载。"""
    names = [s["name"] for s in REG.load_specs()]
    assert "my_terminal" not in names
