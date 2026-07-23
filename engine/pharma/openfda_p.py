"""openFDA provider（免费，无需 key）：药品批准/标签数据。

API: https://api.fda.gov/drug/drugsfda.json  /  /drug/label.json
用于 LOE 分析的批准信息与专利/独占线索（精确专利到期属付费源）。
"""
from __future__ import annotations
from base import Provider

DRUGSFDA = "https://api.fda.gov/drug/drugsfda.json"


class OpenFDAProvider(Provider):
    name = "openfda"
    supports_markets = ("A", "HK", "US")

    def get_approvals(self, resolved, brand_or_generic=None):
        """按药名查批准。修复：原版用 brand_name 字段查公司名（如"恒瑞"），
        必然为空——公司名不是品牌名。改为同时尝试 generic_name 与 brand_name，
        且只在传入具体药名时查询（公司层调用应逐药名传入 brand_or_generic）。"""
        import requests
        term = brand_or_generic
        if not term:
            # 未传药名时不拿公司名瞎查——返回空并说明，而非返回误导性结果
            return {"source": "openFDA", "approvals": [],
                    "note": "未提供具体药名；openFDA 按药名而非公司名检索，"
                            "公司层批准信息需逐个管线药名查询"}
        params = {"search": f'(openfda.generic_name:"{term}" OR openfda.brand_name:"{term}")',
                  "limit": 10}
        try:
            r = requests.get(DRUGSFDA, params=params, timeout=20)
            if r.status_code == 404:
                return {"source": "openFDA", "approvals": []}
            r.raise_for_status()
            js = r.json()
        except Exception as e:
            return {"source": "openFDA", "approvals": [], "error": str(e)}
        approvals = []
        for res in js.get("results", []):
            for p in res.get("products", []):
                approvals.append({
                    "brand_name": (p.get("brand_name")),
                    "marketing_status": p.get("marketing_status"),
                    "application_number": res.get("application_number"),
                    "sponsor_name": res.get("sponsor_name"),
                })
        return {"source": "openFDA", "approvals": approvals}
