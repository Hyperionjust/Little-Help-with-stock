"""付费适配器注册 + 认证发现 + Tier 提升（方案 §4.4，阶段2-2）。

启动流程：
  discover() → 扫描 adapters/*.yaml → 逐个检查认证是否就绪（env/文件/探测）
             → 返回 [可用付费适配器]
  build_paid_prefix(market) → 该市场可用的付费源，置于降级链链首（Tier-0）

不配任何 key 时 discover() 返回空，降级链与阶段1完全一致——这是硬要求，
回归会验证"无 key 时行为不变"。

信任纪律：付费不等于免信任。付费源升 Tier-0，但 price 与 market_cap 仍
至少与一个免费源交叉校验（由 fetch_data 的跨源逻辑 + quality_gate C2/C6 保证）。
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", ".."))
import _paths  # noqa: F401,E402

_ADAPTERS_DIR = os.path.join(_HERE, "adapters")


def _load_yaml(path):
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def load_specs():
    """读取全部适配器 YAML。返回 [spec]。缺 pyyaml 或目录为空时返回 []。"""
    if not os.path.isdir(_ADAPTERS_DIR):
        return []
    specs = []
    for fn in sorted(os.listdir(_ADAPTERS_DIR)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        if fn.startswith("custom.template"):
            continue  # 模板不是可用适配器
        spec = _load_yaml(os.path.join(_ADAPTERS_DIR, fn))
        if spec and spec.get("name"):
            spec.setdefault("_file", fn)
            specs.append(spec)
    return specs


def discover(verbose=False):
    """返回当前认证就绪的付费 DeclarativeProvider 列表。"""
    from adapter import DeclarativeProvider
    out = []
    for spec in load_specs():
        try:
            p = DeclarativeProvider(spec)
            if p.available():
                out.append(p)
                if verbose:
                    print(f"[paid] 可用: {p.display_name} (tier={p.tier}, "
                          f"markets={p.supports_markets})")
        except Exception as e:
            if verbose:
                print(f"[paid] 跳过 {spec.get('name')}: {e}")
    return out


def build_paid_prefix(market, discovered=None):
    """该市场可用的付费源（升序 tier），作为降级链前缀。

    无可用付费源时返回 []，调用方（fetch_data.build_chain）行为与阶段1一致。
    """
    provs = discovered if discovered is not None else discover()
    hit = [p for p in provs if market in p.supports_markets]
    return sorted(hit, key=lambda p: p.tier)


def data_tier(discovered=None, market=None):
    """当前数据层级。有可用付费源 → 0；否则 1（免费链）。供 analysis.meta 标注。"""
    prefix = build_paid_prefix(market, discovered) if market else (
        discovered if discovered is not None else discover())
    return 0 if prefix else 1


def active_adapter_names(discovered=None):
    provs = discovered if discovered is not None else discover()
    return [p.name for p in provs]


if __name__ == "__main__":
    found = discover(verbose=True)
    if not found:
        print("未发现任何已配置的付费终端。免费链开箱即用，无需任何 key。")
        print("接入方法见 references/paid-terminal-guide.md")
    else:
        print(f"\n共 {len(found)} 个付费适配器就绪。")
