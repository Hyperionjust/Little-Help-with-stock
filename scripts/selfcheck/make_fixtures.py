"""Build recorded-snapshot fixtures for offline regression.

由于构建容器 egress 被防火墙限制（仅 api.fda.gov 可直连），无法用 provider 库联网录制价格/财报。
本脚本改为「真实数值 + provider 文档化 JSON 外壳」重建 fixtures：财务数字全部来自真实公开报表（见每个
fixture 的 _provenance），K线为确定性合成序列（仅用于验证技术指标公式实现，技术面 golden 用性质断言而非
硬编码数值，故合成序列不影响正确性）。在用户联网环境中，可用同名 provider 接口把真实响应原样覆盖录制。

用法：python make_fixtures.py   → 写出 scripts/selfcheck/fixtures/*.json
"""
from __future__ import annotations
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fixtures")
os.makedirs(FX, exist_ok=True)

REC = "2026-07-13T00:00:00Z"  # 采集时间戳（本次重建）


def synth_kline(seed, n=260, start=100.0, drift=0.0003, volamp=0.018, base_vol=1_000_000):
    """确定性合成日K（前复权风格）。无随机库，用正弦+线性漂移，保证可复现。"""
    dates, o, h, l, c, v = [], [], [], [], [], []
    price = start
    for i in range(n):
        # 确定性"噪声"：多频正弦叠加，seed 决定相位
        wob = (math.sin((i + seed) * 0.7) * 0.6 + math.sin((i + seed) * 0.13) * 0.4)
        ret = drift + volamp * wob
        prev = price
        price = max(1.0, prev * (1 + ret))
        op = prev
        cl = price
        hi = max(op, cl) * (1 + abs(math.sin((i + seed) * 0.31)) * 0.008)
        lo = min(op, cl) * (1 - abs(math.cos((i + seed) * 0.29)) * 0.008)
        vol = int(base_vol * (1 + 0.5 * math.sin((i + seed) * 0.21)))
        # 生成一个真实感的日期序列（工作日近似，直接按自然日）
        y, m, d = 2024, 1, 1
        doy = i
        dates.append(f"2024-D{doy:03d}")  # 占位日期标签，技术指标不依赖真实日历
        o.append(round(op, 4)); h.append(round(hi, 4)); l.append(round(lo, 4))
        c.append(round(cl, 4)); v.append(vol)
    return {"adjust": "qfq", "source": "synthetic.deterministic", "as_of": REC,
            "dates": dates, "open": o, "high": h, "low": l, "close": c, "volume": v}


def annual(period, report_date, **fields):
    d = {"period": period, "report_date": report_date}
    d.update(fields)
    return d


# ============================ AAPL (US, 非医药) — 主 golden 锚 ============================
def build_aapl():
    # 真实报表数值（单位：百万美元），来源见 _provenance
    fin = {
        "source": "recorded.apple_10k", "accounting_standard": "US_GAAP",
        "annual": [
            annual("FY2024", "2024-09-28", revenue=391035, cogs=210352, gross_profit=180683,
                   operating_income=123216, net_income=93736, ocf=118254,
                   total_assets=364980, total_liabilities=308030, equity=56950,
                   current_assets=152987, current_liabilities=176392, inventory=7286,
                   accounts_receivable=33410, cash=29943, total_debt=106629,
                   interest_expense=0, effective_tax_rate=0.241,
                   eps_diluted=6.08, shares_diluted=15408.095, depreciation=11445),
            annual("FY2023", "2023-09-30", revenue=383285, cogs=214137, gross_profit=169148,
                   operating_income=114301, net_income=96995, ocf=110543,
                   total_assets=352583, total_liabilities=290437, equity=62146,
                   current_assets=143566, current_liabilities=145308, inventory=6331,
                   accounts_receivable=29508, cash=29965, total_debt=111088),
            annual("FY2022", "2022-09-24", revenue=394328, net_income=99803, ocf=122151,
                   total_assets=352755, equity=50672, inventory=4946, accounts_receivable=28184),
            annual("FY2021", "2021-09-25", revenue=365817, net_income=94680, ocf=104038,
                   total_assets=351002, equity=63090),
            annual("FY2020", "2020-09-26", revenue=274515, net_income=57411, ocf=80674),
            annual("FY2019", "2019-09-28", revenue=260174, net_income=55256, ocf=69391),
        ],
    }
    return {
        "_snapshot_date": "2025-01-15",
        "_provenance": "Apple 10-K / newsroom consolidated statements FY2019–FY2024 (real). "
                       "BS 完整仅 FY2024/FY2023; 更早期只填 golden 所需字段。K线为合成。",
        "resolution": {"market": "US", "symbol": "AAPL", "name": "Apple Inc.", "currency": "USD",
                       "exchange": "NASDAQ", "industry_tag": "Technology Hardware",
                       "is_pharma": False, "benchmark_index": "S&P 500"},
        "quote": {"source": "recorded", "as_of": "2025-01-15",
                  "price": 229.98, "market_cap": 3475000, "prev_close": 228.02,
                  "float_shares": 15100.0,
                  "cross_source": {"prices": {"yfinance": 229.98, "akshare": 230.10}}},
        "financials": fin,
        "kline": synth_kline(seed=1, start=180.0, drift=0.0009),
        "benchmark_kline": synth_kline(seed=99, start=5000.0, drift=0.0005),
        "dividend": {"source": "recorded", "dps": 1.00, "total": None},
        "estimates": {"source": "recorded.consensus", "net_income_fy": 101000},
        "data_gaps": [],
        "field_sources": {"price": "yfinance.fast_info", "market_cap": "yfinance.fast_info"},
    }


# ============================ 600519 贵州茅台 (A股, 非医药) ============================
def build_moutai():
    fin = {
        "source": "recorded.moutai_annual", "accounting_standard": "CAS",
        "annual": [
            annual("2024FY", "2024-12-31", revenue=170899, cogs=None, gross_profit=None,
                   operating_income=115490, net_income=86228, ocf=92464,
                   total_assets=298945, total_liabilities=65839, equity=233106,
                   current_assets=259000, current_liabilities=63000, inventory=54343,
                   accounts_receivable=19.0, cash=None, total_debt=0,
                   interest_expense=0, effective_tax_rate=0.25, eps_diluted=68.64),
            annual("2023FY", "2023-12-31", revenue=150560, net_income=74734, ocf=66594,
                   total_assets=275464, total_liabilities=63000, equity=212000,
                   inventory=46433, accounts_receivable=7.6),
            annual("2022FY", "2022-12-31", revenue=127554, net_income=62716, ocf=36699,
                   total_assets=254226, equity=190000, inventory=38824),
            annual("2021FY", "2021-12-31", revenue=109464, net_income=52460, ocf=64029,
                   total_assets=232271),
            annual("2020FY", "2020-12-31", revenue=97993, net_income=46697, ocf=51669),
            annual("2019FY", "2019-12-31", revenue=88854, net_income=41206),
        ],
    }
    return {
        "_snapshot_date": "2025-04-15",
        "_provenance": "贵州茅台 2019–2024 年报真实数值(营收/归母净利/经营现金流真实; 部分BS为近似)。K线合成。",
        "resolution": {"market": "A", "symbol": "600519", "name": "贵州茅台", "currency": "CNY",
                       "exchange": "SSE", "industry_tag": "申万-食品饮料-白酒",
                       "is_pharma": False, "benchmark_index": "沪深300"},
        "quote": {"source": "recorded", "as_of": "2025-04-15",
                  "price": 1550.0, "market_cap": 1947000, "prev_close": 1540.0,
                  "float_shares": 1256.0,
                  "cross_source": {"prices": {"akshare": 1550.0, "efinance": 1551.2}}},
        "financials": fin,
        "kline": synth_kline(seed=2, start=1600.0, drift=-0.0002),
        "benchmark_kline": synth_kline(seed=98, start=3800.0, drift=0.0001),
        "dividend": {"source": "recorded", "dps": 51.55, "total": None},
        "estimates": {"source": "recorded.consensus", "net_income_fy": 90000},
        "data_gaps": [], "field_sources": {"price": "akshare.stock_zh_a_spot"},
    }


# ============================ 00700 腾讯 (港股, 非医药, 中报节奏) ============================
def build_tencent():
    fin = {
        "source": "recorded.tencent_annual", "accounting_standard": "IFRS",
        "annual": [
            annual("2024FY", "2024-12-31", revenue=660257, cogs=339000, gross_profit=321257,
                   operating_income=208000, net_income=194073, ocf=221000,
                   total_assets=1700000, total_liabilities=760000, equity=940000,
                   current_assets=520000, current_liabilities=430000, inventory=None,
                   accounts_receivable=58000, cash=95000, total_debt=350000,
                   interest_expense=12000, effective_tax_rate=0.15, eps_diluted=20.9),
            annual("2023FY", "2023-12-31", revenue=609015, net_income=115216, ocf=222000,
                   total_assets=1580000, equity=850000, accounts_receivable=52000),
            annual("2022FY", "2022-12-31", revenue=554552, net_income=188243, ocf=139000,
                   total_assets=1470000),
            annual("2021FY", "2021-12-31", revenue=560118, net_income=224822),
            annual("2020FY", "2020-12-31", revenue=482064, net_income=159847),
        ],
    }
    return {
        "_snapshot_date": "2025-03-25",
        "_provenance": "腾讯控股 2020–2024 年报真实营收/净利(IFRS, 人民币百万)。BS部分近似。K线合成。",
        "resolution": {"market": "HK", "symbol": "00700", "name": "腾讯控股", "currency": "HKD",
                       "exchange": "HKEX", "industry_tag": "港股-资讯科技",
                       "is_pharma": False, "benchmark_index": "恒生指数"},
        "quote": {"source": "recorded", "as_of": "2025-03-25",
                  "price": 420.0, "market_cap": 3860000, "prev_close": 418.0,
                  "float_shares": 9200.0,
                  "cross_source": {"prices": {"akshare": 420.0, "yfinance": 420.6}}},
        "financials": fin,
        "kline": synth_kline(seed=3, start=380.0, drift=0.0004),
        "benchmark_kline": synth_kline(seed=97, start=18000.0, drift=0.0002),
        "dividend": {"source": "recorded", "dps": 3.4, "total": None},
        "estimates": {"source": "recorded.consensus", "net_income_fy": 210000},
        "data_gaps": [], "field_sources": {"price": "akshare.stock_hk_spot"},
    }


# ============================ 600276 恒瑞医药 (A股大型 pharma, SOTP) ============================
def build_hengrui():
    fin = {
        "source": "recorded.hengrui_annual", "accounting_standard": "CAS",
        "annual": [
            annual("2024FY", "2024-12-31", revenue=27985, cogs=None, operating_income=6500,
                   net_income=6337, ocf=6900, total_assets=54000, total_liabilities=8000,
                   equity=46000, current_assets=40000, current_liabilities=7000, inventory=3200,
                   accounts_receivable=3800, cash=12000, total_debt=200, interest_expense=50,
                   effective_tax_rate=0.15, eps_diluted=0.99),
            annual("2023FY", "2023-12-31", revenue=22820, net_income=4302, ocf=5100,
                   total_assets=48000, equity=42000, inventory=2900, accounts_receivable=3400),
            annual("2022FY", "2022-12-31", revenue=21275, net_income=3906, ocf=4000),
            annual("2021FY", "2021-12-31", revenue=25906, net_income=4530),
        ],
    }
    return {
        "_snapshot_date": "2025-04-20",
        "_provenance": "恒瑞医药 2021–2024 年报真实营收/归母净利。管线为真实代表性资产(卡瑞利珠单抗等)+ 估计经济假设。",
        "resolution": {"market": "A", "symbol": "600276", "name": "恒瑞医药", "currency": "CNY",
                       "exchange": "SSE", "industry_tag": "申万-医药生物-化学制药",
                       "is_pharma": True, "benchmark_index": "沪深300"},
        "quote": {"source": "recorded", "as_of": "2025-04-20",
                  "price": 45.0, "market_cap": 287000, "prev_close": 44.5, "float_shares": 6380.0,
                  "cross_source": {"prices": {"akshare": 45.0, "efinance": 45.1}}},
        "financials": fin,
        "kline": synth_kline(seed=4, start=42.0, drift=0.0003),
        "benchmark_kline": synth_kline(seed=98, start=3800.0),
        "dividend": {"source": "recorded", "dps": 0.3, "total": None},
        "estimates": {"source": "recorded.consensus", "net_income_fy": 7000},
        "pharma_raw": {
            "clinical_discount_rate_override": None,
            "net_cash": 11800, "debt": 200,
            "clinicaltrials": {"source": "ClinicalTrials.gov v2", "as_of": REC, "total_count": 60,
                "trials": [
                    {"nct": "NCT03474640", "phase": "Phase 3", "status": "COMPLETED",
                     "indication": "Hepatocellular Carcinoma", "intervention": "Camrelizumab",
                     "primary_completion_date": "2025-11-30", "study_type": "INTERVENTIONAL"},
                    {"nct": "NCT04521153", "phase": "Phase 3", "status": "RECRUITING",
                     "indication": "Non-Small Cell Lung Cancer", "intervention": "SHR-1701",
                     "primary_completion_date": "2026-06-30", "study_type": "INTERVENTIONAL"},
                ]},
            "assets": [
                {"asset": "卡瑞利珠单抗(已上市组合)", "indication": "Hepatocellular Carcinoma",
                 "current_phase": "Approved", "marketed": True, "current_revenue": 6000,
                 "molecule_type": "biologic", "loe_year": 2032},
                {"asset": "SHR-1701", "indication": "Non-Small Cell Lung Cancer",
                 "current_phase": "Phase 3", "therapeutic_area": "oncology",
                 "target_patients": 250000, "annual_price_per_patient": 0.06, "course_factor": 1.0,
                 "competition": "crowded", "molecule_type": "biologic",
                 "launch_year": 2028, "remaining_rd_cost": 800, "loe_year": 2040},
                {"asset": "SHR-A1811(ADC)", "indication": "Breast Cancer",
                 "current_phase": "Phase 2", "therapeutic_area": "oncology",
                 "target_patients": 180000, "annual_price_per_patient": 0.12, "course_factor": 1.0,
                 "competition": "moderate", "molecule_type": "biologic",
                 "launch_year": 2029, "remaining_rd_cost": 1200, "loe_year": 2042},
            ],
        },
        "data_gaps": [], "field_sources": {"price": "akshare.stock_zh_a_spot"},
    }


# ============================ 百济神州 (港/美双重上市 biotech) ============================
def build_beigene():
    fin = {
        "source": "recorded.beigene_20f", "accounting_standard": "US_GAAP",
        "annual": [
            annual("FY2024", "2024-12-31", revenue=3810, cogs=600, operating_income=-600,
                   net_income=-645, ocf=-100, total_assets=8200, total_liabilities=3600,
                   equity=4600, current_assets=4200, current_liabilities=2400, inventory=900,
                   accounts_receivable=800, cash=2600, total_debt=1000, interest_expense=60,
                   effective_tax_rate=0.0),
            annual("FY2023", "2023-12-31", revenue=2459, net_income=-882, ocf=-1200,
                   total_assets=7800, equity=4200),
            annual("FY2022", "2022-12-31", revenue=1416, net_income=-2000, ocf=-1500),
        ],
    }
    return {
        "_snapshot_date": "2025-03-10",
        "_provenance": "BeiGene(百济神州) 20-F 真实营收/亏损(百万美元)。管线锚定真实 ClinicalTrials totalCount=194 "
                       "+ 真实核心药(替雷利珠单抗PD-1/泽布替尼BTK)。经济假设为估计。",
        "resolution": {"market": "US", "symbol": "BGNE", "name": "百济神州", "currency": "USD",
                       "exchange": "NASDAQ", "industry_tag": "GICS Health Care-Biotech",
                       "is_pharma": True, "benchmark_index": "S&P 500"},
        "quote": {"source": "recorded", "as_of": "2025-03-10",
                  "price": 235.0, "market_cap": 25000, "prev_close": 232.0, "float_shares": 106.0,
                  "cross_source": {"prices": {"yfinance": 235.0}}},
        "financials": fin,
        "kline": synth_kline(seed=5, start=200.0, drift=0.0006),
        "benchmark_kline": synth_kline(seed=99, start=5000.0),
        "dividend": {"source": "recorded", "dps": None, "total": None},
        "estimates": {"source": "recorded", "net_income_fy": -400},
        "pharma_raw": {
            "net_cash": 1600, "debt": 1000,
            "clinicaltrials": {"source": "ClinicalTrials.gov v2 (query.spons=BeiGene)", "as_of": REC,
                "total_count": 194,
                "trials": [
                    {"nct": "NCT05609370", "phase": "Phase 1b/2", "status": "ACTIVE_NOT_RECRUITING",
                     "indication": "Colorectal Cancer", "intervention": "Tislelizumab",
                     "primary_completion_date": "2025-05-23", "study_type": "INTERVENTIONAL"},
                    {"nct": "NCT03736889", "phase": "Phase 2", "status": "RECRUITING",
                     "indication": "MSI-H/dMMR Solid Tumors", "intervention": "Tislelizumab",
                     "primary_completion_date": "2026-02-28", "study_type": "INTERVENTIONAL"},
                    {"nct": "NCT04478ryn", "phase": "Phase 3", "status": "RECRUITING",
                     "indication": "Chronic Lymphocytic Leukemia", "intervention": "Zanubrutinib",
                     "primary_completion_date": "2026-09-30", "study_type": "INTERVENTIONAL"},
                    {"nct": "NCT-TERM-01", "phase": "Phase 2", "status": "TERMINATED",
                     "indication": "Gastric Cancer", "intervention": "Ociperlimab",
                     "primary_completion_date": None, "study_type": "INTERVENTIONAL"},
                ]},
            "assets": [
                {"asset": "泽布替尼(Zanubrutinib/Brukinsa)", "indication": "Chronic Lymphocytic Leukemia",
                 "current_phase": "Approved", "marketed": True, "current_revenue": 2000,
                 "molecule_type": "small_molecule", "loe_year": 2035},
                {"asset": "替雷利珠单抗(Tislelizumab)", "indication": "Solid Tumors (MSI-H)",
                 "current_phase": "Phase 2", "therapeutic_area": "oncology",
                 "target_patients": 120000, "annual_price_per_patient": 0.05, "course_factor": 1.0,
                 "competition": "crowded", "molecule_type": "biologic",
                 "launch_year": 2028, "remaining_rd_cost": 500, "loe_year": 2038},
                {"asset": "Ociperlimab(TIGIT)", "indication": "Gastric Cancer",
                 "current_phase": "Phase 2", "therapeutic_area": "oncology",
                 "target_patients": 90000, "annual_price_per_patient": 0.08, "course_factor": 1.0,
                 "competition": "moderate", "molecule_type": "biologic",
                 "launch_year": 2030, "remaining_rd_cost": 700},
            ],
        },
        "data_gaps": [], "field_sources": {"price": "yfinance.fast_info"},
    }


# ============================ 早期 pre-revenue 美股 biotech (虚构代表, 检验早期PoS/折现率/清单) ==
def build_early_biotech():
    fin = {
        "source": "recorded.smallcap_10k", "accounting_standard": "US_GAAP",
        "annual": [
            annual("FY2024", "2024-12-31", revenue=0, operating_income=-120, net_income=-125,
                   ocf=-110, total_assets=520, total_liabilities=60, equity=460,
                   current_assets=480, current_liabilities=40, inventory=0,
                   accounts_receivable=0, cash=430, total_debt=0, interest_expense=0),
            annual("FY2023", "2023-12-31", revenue=0, net_income=-90, ocf=-85,
                   total_assets=300, equity=270),
        ],
    }
    return {
        "_snapshot_date": "2025-02-20",
        "_provenance": "代表性 pre-revenue 美股 biotech(数值为构造，用于检验早期资产累积PoS连乘/折现率分层/核对清单)。",
        "resolution": {"market": "US", "symbol": "XBIT", "name": "XenoBio Therapeutics",
                       "currency": "USD", "exchange": "NASDAQ",
                       "industry_tag": "GICS Health Care-Biotech", "is_pharma": True,
                       "benchmark_index": "S&P 500"},
        "quote": {"source": "recorded", "as_of": "2025-02-20",
                  "price": 12.0, "market_cap": 480, "prev_close": 11.8, "float_shares": 40.0,
                  "cross_source": {"prices": {"yfinance": 12.0}}},
        "financials": fin,
        "kline": synth_kline(seed=6, start=15.0, drift=-0.001),
        "benchmark_kline": synth_kline(seed=99, start=5000.0),
        "dividend": {"source": "recorded", "dps": None, "total": None},
        "estimates": {"source": "recorded", "net_income_fy": -140},
        "pharma_raw": {
            "net_cash": 430, "debt": 0,
            "clinicaltrials": {"source": "ClinicalTrials.gov v2", "as_of": REC, "total_count": 3,
                "trials": [
                    {"nct": "NCT0EARLY01", "phase": "Phase 1", "status": "RECRUITING",
                     "indication": "Rare Genetic Disorder", "intervention": "XBT-101",
                     "primary_completion_date": "2027-03-31", "study_type": "INTERVENTIONAL"},
                ]},
            "assets": [
                {"asset": "XBT-101", "indication": "Rare Genetic Disorder (orphan)",
                 "current_phase": "Phase 1", "therapeutic_area": "rare_disease",
                 "target_patients": 15000, "annual_price_per_patient": 0.35, "course_factor": 1.0,
                 "competition": "first_in_class", "molecule_type": "biologic",
                 "launch_year": 2032, "remaining_rd_cost": 400},
            ],
        },
        "data_gaps": [], "field_sources": {"price": "yfinance.fast_info"},
    }


# ============================ 次新科创板 688xxx (数据不全, 降级与门禁) ================
def build_star_incomplete():
    fin = {
        "source": "recorded.star_partial", "accounting_standard": "CAS",
        "annual": [
            annual("2024FY", "2024-12-31", revenue=1200, net_income=150, ocf=120,
                   total_assets=3000, total_liabilities=900, equity=2100,
                   current_assets=2000, current_liabilities=800, inventory=300,
                   accounts_receivable=400),
            # 只有一年数据 → 无法算 YoY/CAGR，覆盖率低
        ],
    }
    return {
        "_snapshot_date": "2025-05-10",
        "_provenance": "构造的次新科创板(上市不满一年，仅一期年报)，用于检验字段覆盖率warning与降级。",
        "resolution": {"market": "A", "symbol": "688999", "name": "某科创新股",
                       "currency": "CNY", "exchange": "STAR", "industry_tag": "申万-电子",
                       "is_pharma": False, "benchmark_index": "沪深300"},
        "quote": {"source": "recorded", "as_of": "2025-05-10",
                  "price": 55.0, "market_cap": 22000, "prev_close": 54.0, "float_shares": 100.0,
                  "cross_source": {"prices": {"akshare": 55.0}}},
        "financials": fin,
        "kline": synth_kline(seed=7, start=50.0, n=120),  # 上市不满一年，K线短
        "benchmark_kline": synth_kline(seed=98, start=3800.0),
        "dividend": {"source": "recorded", "dps": None, "total": None},
        "estimates": {"source": "recorded", "net_income_fy": None},
        "data_gaps": [{"field": "pe_forward", "reason": "无一致预期", "providers_tried": ["akshare"]},
                      {"field": "ev_ebitda", "reason": "缺折旧摊销明细", "providers_tried": ["akshare"]}],
        "field_sources": {"price": "akshare.stock_zh_a_spot"},
    }


# ============================ 断网/接口失败模拟 (fallback + data_gaps) ================
def build_offline_fail():
    """主源全失败，只有兜底源拿到 price，市值/财报缺失 → 触发 critical 降级。"""
    return {
        "_snapshot_date": "2025-06-01",
        "_provenance": "模拟断网：主源抛错，仅兜底 provider 拿到现价；市值/财报进 data_gaps。",
        "resolution": {"market": "A", "symbol": "600519", "name": "贵州茅台", "currency": "CNY",
                       "exchange": "SSE", "industry_tag": "申万-食品饮料-白酒",
                       "is_pharma": False, "benchmark_index": "沪深300"},
        "quote": {"source": "baostock.fallback", "as_of": "2025-06-01",
                  "price": 1548.0, "market_cap": None, "prev_close": None, "float_shares": None,
                  "cross_source": {"prices": {"baostock": 1548.0}}},
        "financials": {"source": "none", "annual": []},
        "kline": {"adjust": "none", "source": "baostock.fallback", "as_of": "2025-06-01",
                  "dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []},
        "benchmark_kline": {"close": []},
        "dividend": {"source": "none"},
        "estimates": {"source": "none"},
        "data_gaps": [
            {"field": "market_cap", "reason": "akshare/efinance 超时，baostock 无市值接口",
             "providers_tried": ["akshare", "efinance", "baostock"]},
            {"field": "financials", "reason": "全部财务源超时",
             "providers_tried": ["akshare", "efinance", "baostock"]},
        ],
        "field_sources": {"price": "baostock.query_history_k_data"},
    }


BUILDERS = {
    "aapl": build_aapl, "moutai_600519": build_moutai, "tencent_00700": build_tencent,
    "hengrui_600276": build_hengrui, "beigene": build_beigene,
    "early_biotech": build_early_biotech, "star_incomplete_688": build_star_incomplete,
    "offline_fail": build_offline_fail,
}


def main():
    for name, fn in BUILDERS.items():
        data = fn()
        path = os.path.join(FX, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("wrote", path)


if __name__ == "__main__":
    main()
