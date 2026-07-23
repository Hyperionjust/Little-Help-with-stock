"""公司 logo 尽力抓取（方案外增补）。

设计红线：
  · **尽力而为 + 优雅省略**——抓不到就返回 None，报告照常渲染，绝不出破图或占位方块。
  · logo 是公司自己的商标，用在关于该公司的研报上属标识性使用（卖方研报惯例）；
    报告页脚附一行"logo 归各自商标权利人所有"作为归属声明。
  · 不做花哨——只在封面右上角放一枚小 logo，克制。

抓取链（免费、无需 key）：
  1. --logo 显式指定的本地文件/URL（最高优先）
  2. 内置"代码/名称 → 域名"注册表 → DuckDuckGo 图标服务（清晰度较好）
  3. Google favicon 服务（保底）
  4. 全失败 → None

返回 data-URI（base64 内嵌），WeasyPrint 直接嵌入，离线可开。
"""
from __future__ import annotations
import base64
import os
import urllib.request

# 内置域名注册表（够用即可；查不到就跳过 logo，不硬猜）。
# 键：股票代码（去后缀）或公司名子串（小写）。值：官网域名。
DOMAIN_REGISTRY = {
    # A股
    "600519": "moutaichina.com", "贵州茅台": "moutaichina.com",
    "688256": "cambricon.com", "寒武纪": "cambricon.com",
    "600276": "hengrui.com", "恒瑞": "hengrui.com",
    "000858": "wuliangye.com.cn", "300760": "mindray.com",
    "601398": "icbc.com.cn", "000333": "midea.com",
    # 港股
    "00700": "tencent.com", "腾讯": "tencent.com",
    "09988": "alibabagroup.com", "03690": "meituan.com",
    # 美股
    "AAPL": "apple.com", "MSFT": "microsoft.com", "NVDA": "nvidia.com",
    "GOOGL": "abc.xyz", "AMZN": "amazon.com", "TSLA": "tesla.com",
}

_ICON_SERVICES = [
    "https://icons.duckduckgo.com/ip3/{domain}.ico",       # 较清晰
    "https://www.google.com/s2/favicons?domain={domain}&sz=128",  # 保底
]

_UA = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 8


def _domain_for(resolved):
    sym = str(resolved.get("symbol", "")).split(".")[0]
    if sym in DOMAIN_REGISTRY:
        return DOMAIN_REGISTRY[sym]
    name = str(resolved.get("name", "")).lower()
    for key, dom in DOMAIN_REGISTRY.items():
        if key.lower() in name and not key.isdigit():
            return dom
    return None


def _fetch_bytes(url):
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "")
        # 太小（<300B）多半是空白/占位图，视为失败
        if data and len(data) > 300:
            return data, ct
    except Exception:
        pass
    return None, None


def _to_data_uri(data, content_type):
    """统一转 PNG 再内嵌。

    为什么强制转 PNG：WeasyPrint 对 .ico（DuckDuckGo/favicon 常返回此格式）解码不稳，
    直接内嵌会静默不渲染（自检时封面右上角空白）。用 Pillow 统一解码为 PNG，
    顺带把过大的图缩到合理尺寸——保证任何来源的图标都能可靠显示。
    Pillow 不可用或解码失败时退回原字节（尽力而为）。
    """
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(data))
        im = im.convert("RGBA")
        # 缩到最长边 ≤256px（封面只占 14mm，够清晰且减小体积）
        if max(im.size) > 256:
            im.thumbnail((256, 256), Image.LANCZOS)
        out = _io.BytesIO()
        im.save(out, format="PNG")
        png = out.getvalue()
        return "data:image/png;base64," + base64.b64encode(png).decode()
    except Exception:
        mime = (content_type.split(";")[0].strip() or "image/png")
        return f"data:{mime};base64," + base64.b64encode(data).decode()


def get_logo(resolved, explicit=None):
    """返回 data-URI 或 None。resolved 为 analysis.resolution。

    explicit: --logo 传入的本地路径或 URL（最高优先）。
    """
    # 1. 显式指定
    if explicit:
        if os.path.exists(explicit):
            with open(explicit, "rb") as f:
                data = f.read()
            ext = os.path.splitext(explicit)[1].lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "svg": "image/svg+xml", "gif": "image/gif",
                    "ico": "image/x-icon"}.get(ext, "image/png")
            return f"data:{mime};base64," + base64.b64encode(data).decode()
        if explicit.startswith("http"):
            data, ct = _fetch_bytes(explicit)
            if data:
                return _to_data_uri(data, ct)
        return None  # 显式给了但拿不到 → 不再自动猜，尊重用户意图

    # 2/3. 域名 → 图标服务链
    domain = _domain_for(resolved)
    if not domain:
        return None
    for tmpl in _ICON_SERVICES:
        data, ct = _fetch_bytes(tmpl.format(domain=domain))
        if data:
            return _to_data_uri(data, ct)
    return None


if __name__ == "__main__":
    import sys, json
    r = json.load(open(sys.argv[1], encoding="utf-8")).get("resolution", {}) \
        if len(sys.argv) > 1 else {"symbol": "AAPL", "name": "Apple"}
    uri = get_logo(r)
    print(f"logo for {r.get('symbol')}: {'✅ ' + str(len(uri)) + ' chars' if uri else '❌ 无（将优雅省略）'}")
