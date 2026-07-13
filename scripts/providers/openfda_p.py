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
        import requests
        term = brand_or_generic or resolved.get("name") or resolved.get("symbol")
        params = {"search": f'openfda.brand_name:"{term}"', "limit": 10}
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
