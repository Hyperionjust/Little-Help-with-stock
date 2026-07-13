"""ClinicalTrials.gov v2 API provider（免费，无需 key）：医药管线/试验阶段。

API: https://clinicaltrials.gov/api/v2/studies?query.spons=<sponsor>
解析出 {nct, phase, status, indication, intervention, primary_completion_date, study_type}。
"""
from __future__ import annotations
from base import Provider

API = "https://clinicaltrials.gov/api/v2/studies"


class ClinicalTrialsProvider(Provider):
    name = "clinicaltrials"
    supports_markets = ("A", "HK", "US")

    def get_clinical(self, resolved, sponsor=None, max_pages=3):
        import requests
        # 优先英文 sponsor 别名（ClinicalTrials 用英文检索）；中文名检索几乎必空
        sponsor = (sponsor or resolved.get("clinical_sponsor")
                   or resolved.get("name") or resolved.get("symbol"))
        trials, total, token = [], None, None
        for _ in range(max_pages):
            # 不传 fields（v2 的 fields 白名单易踩 400）→ 取完整记录，解析 protocolSection，稳。
            params = {"query.spons": sponsor, "pageSize": 100, "countTotal": "true"}
            if token:
                params["pageToken"] = token
            r = requests.get(API, params=params,
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            js = r.json()
            total = js.get("totalCount", total)
            for st in js.get("studies", []):
                ps = st.get("protocolSection", {})
                idm = ps.get("identificationModule", {})
                stm = ps.get("statusModule", {})
                dm = ps.get("designModule", {})
                cm = ps.get("conditionsModule", {})
                aim = ps.get("armsInterventionsModule", {})
                trials.append({
                    "nct": idm.get("nctId"),
                    "phase": ", ".join(dm.get("phases", []) or []) or None,
                    "status": stm.get("overallStatus"),
                    "indication": (cm.get("conditions") or [None])[0],
                    "intervention": (([i.get("name") for i in aim.get("interventions", [])] or [None])[0]),
                    "primary_completion_date": (stm.get("primaryCompletionDateStruct", {}) or {}).get("date"),
                    "study_type": dm.get("studyType"),
                })
            token = js.get("nextPageToken")
            if not token:
                break
        return {"source": "ClinicalTrials.gov v2", "total_count": total, "trials": trials}
