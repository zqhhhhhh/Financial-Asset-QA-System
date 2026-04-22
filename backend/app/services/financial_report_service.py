"""
Financial report retrieval service.

Primary source  : yfinance quarterly financials (US, HK, partial CN)
Filing links    : SEC EDGAR (US) / CNINFO (A-shares)
HK filing links : not available via public API
"""
import json
import logging
import re
import time
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

from app.services.llm_service import LLMService
from app.services.ticker_resolver import TickerResolverService
from app.services.web_search_service import WebSearchService

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────

_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_SUBMISSIONS  = "https://data.sec.gov/submissions/CIK{:010d}.json"
_CNINFO_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_PDF = "http://static.cninfo.com.cn/{adjunct_url}"
_TIMEOUT = 10
_EDGAR_HEADERS = {
    "User-Agent": "FinancialQABot/1.0 (educational; contact: admin@example.com)",
    "Accept-Encoding": "gzip, deflate",
}

# Key income-statement metrics to surface
_KEY_METRICS = [
    ("Total Revenue",      "总营收"),
    ("Gross Profit",       "毛利润"),
    ("Operating Income",   "营业利润"),
    ("Net Income",         "净利润"),
    ("EBITDA",             "EBITDA"),
    ("Basic EPS",          "基本每股收益"),
]

REPORT_KEYWORDS = {
    "财报", "季报", "年报", "半年报", "财务报告", "年度报告", "季度报告",
    "10-k", "10-q", "10k", "10q", "annual report", "quarterly report",
    "earnings", "盈利报告", "财务数据", "利润表", "营收", "净利润", "eps",
}

_QUARTERS_PROMPT = """从用户问题中判断需要查询几个季度的财报，返回 JSON：

{"quarters": N}   # N 为整数 1-8

规则：
- 最近季度 / 最新季度 / 上季度 / 没有明确说几个 → {"quarters": 1}
- 最近两季度 / 半年 → {"quarters": 2}
- 最近三季度 → {"quarters": 3}
- 一年 / 全年 / 年报 / 四个季度 / 去年 / 上一年 / 今年 / 某某年（如2024年）→ {"quarters": 4}
- 两年 / 近两年 → {"quarters": 8}

只返回 JSON，不要其他内容。"""


def is_report_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in REPORT_KEYWORDS)


def _fmt_value(val: float, currency: str) -> str:
    """Format a raw float (usually in base currency units) to readable string."""
    if abs(val) >= 1e12:
        return f"{val/1e12:.2f}万亿{currency}"
    if abs(val) >= 1e8:
        return f"{val/1e8:.2f}亿{currency}"
    if abs(val) >= 1e6:
        return f"{val/1e6:.2f}百万{currency}"
    return f"{val:,.0f}{currency}"


class FinancialReportService:
    # Class-level EDGAR ticker→CIK cache
    _edgar_cache: dict[str, int] = {}
    _edgar_cache_loaded: bool = False

    def __init__(
        self,
        ticker_resolver: TickerResolverService,
        llm_service: LLMService | None = None,
        web_search: WebSearchService | None = None,
    ) -> None:
        self.resolver = ticker_resolver
        self.llm = llm_service
        self.web_search = web_search or WebSearchService()

    # ── quarter count extraction ───────────────────────────────────────────────

    def _extract_quarters(self, query: str) -> int:
        """Use LLM to determine how many quarters the user wants. Default: 1."""
        if self.llm and self.llm.available:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": _QUARTERS_PROMPT},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=20,
                thinking=False,
            ).strip()
            m = re.search(r"\{.*?\}", raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group())
                    n = int(obj.get("quarters", 1))
                    return max(1, min(n, 8))
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass

        # keyword fallback
        q = query.lower()
        if any(k in q for k in ["一年", "全年", "年报", "四个季度", "去年", "上一年", "今年"]):
            return 4
        if re.search(r"20\d{2}年", q):  # 提到具体年份
            return 4
        if any(k in q for k in ["半年", "两个季度", "两季"]):
            return 2
        return 1

    def fetch(self, query: str) -> list[dict]:
        """
        Resolve ticker + determine quarter count from query, then fetch financial data.
        Returns list of dicts: {title, date, content, url, source}
        """
        ticker = self.resolver.resolve(query)
        if not ticker:
            logger.info("[fin_report] cannot resolve ticker")
            return []

        n_quarters = self._extract_quarters(query)
        logger.info("[fin_report] ticker=%s n_quarters=%d", ticker, n_quarters)

        results: list[dict] = []

        # ── yfinance: actual financial numbers (primary) ──────────────────
        yf_data = self._fetch_yfinance(ticker, n_quarters)
        results.extend(yf_data)

        # ── filing links: supplementary ────────────────────────────────────
        if ticker.endswith(".SS") or ticker.endswith(".SZ"):
            links = self._fetch_cninfo(ticker.split(".")[0], n_quarters)
        elif ticker.endswith(".HK"):
            links = self._fetch_hk_search(ticker[:-3])
        else:
            links = self._fetch_edgar(ticker, n_quarters)
        results.extend(links)

        # ── Yahoo Finance financials page (universal fallback link) ────────
        results.append({
            "title": f"查看完整财务报表（Yahoo Finance）",
            "date": "",
            "content": "",
            "url": f"https://finance.yahoo.com/quote/{ticker}/financials/",
            "source": "Yahoo Finance",
        })

        logger.info("[fin_report] total items=%d for %s", len(results), ticker)
        return results

    # ── yfinance financial data ────────────────────────────────────────────────

    def _fetch_yfinance(self, ticker: str, n_quarters: int = 1) -> list[dict]:
        currency_map = {
            ".HK": "港元", ".SS": "人民币", ".SZ": "人民币",
        }
        currency = next(
            (v for k, v in currency_map.items() if ticker.endswith(k)), "美元"
        )

        try:
            tkr = yf.Ticker(ticker)

            def _growth(curr: float, prev: float) -> str:
                """返回同比/环比字符串，如 '+12.3%' 或 '-5.1%'。"""
                if prev == 0:
                    return ""
                g = (curr - prev) / abs(prev) * 100
                return f"+{g:.1f}%" if g >= 0 else f"{g:.1f}%"

            def _extract_results(stmt: pd.DataFrame, label: str, src: str,
                                  is_quarterly: bool) -> list[dict]:
                """
                从 stmt 提取前 n_quarters 期数据，并附带 YoY（同比）和 QoQ（环比）。
                is_quarterly=True  → YoY 对比 4 列前（同季度去年），QoQ 对比 1 列前
                is_quarterly=False → YoY 对比 1 列前（去年同期）
                """
                results = []
                ncols = len(stmt.columns)
                for i, col in enumerate(stmt.columns[:n_quarters]):
                    period = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
                    lines = []
                    for eng, chn in _KEY_METRICS:
                        if eng not in stmt.index:
                            continue
                        val = stmt.loc[eng, col]
                        if pd.isna(val):
                            continue
                        curr = float(val)
                        parts = [f"  {chn}: {_fmt_value(curr, currency)}"]
                        # YoY
                        yoy_i = i + (4 if is_quarterly else 1)
                        if yoy_i < ncols:
                            prev = stmt.loc[eng, stmt.columns[yoy_i]]
                            if pd.notna(prev) and float(prev) != 0:
                                parts.append(f"（同比 {_growth(curr, float(prev))}）")
                        # QoQ（仅季度）
                        if is_quarterly and i + 1 < ncols:
                            prev_q = stmt.loc[eng, stmt.columns[i + 1]]
                            if pd.notna(prev_q) and float(prev_q) != 0:
                                parts.append(f"（环比 {_growth(curr, float(prev_q))}）")
                        lines.append("".join(parts))
                    if lines:
                        results.append({
                            "title": f"{label}（{period}）",
                            "date": period,
                            "content": "\n".join(lines),
                            "url": "",
                            "source": src,
                        })
                return results

            _MIN_METRICS = 2  # 季度数据至少需要这么多关键指标才算有效

            # ── 1. 尝试季度报表 ──────────────────────────────────────────
            q_stmt = tkr.quarterly_income_stmt
            if q_stmt is not None and not q_stmt.empty:
                latest_col = q_stmt.columns[0]
                valid_count = sum(
                    1 for eng, _ in _KEY_METRICS
                    if eng in q_stmt.index and pd.notna(q_stmt.loc[eng, latest_col])
                )
                if valid_count >= _MIN_METRICS:
                    results = _extract_results(q_stmt, "季度财报摘要", "yfinance 季度财务数据",
                                               is_quarterly=True)
                    if results:
                        logger.info("[fin_report] yfinance quarterly: %d periods for %s", len(results), ticker)
                        return results
                else:
                    logger.info("[fin_report] %s quarterly sparse (%d metrics), using annual", ticker, valid_count)

            # ── 2. 回退年度报表 ──────────────────────────────────────────
            a_stmt = tkr.income_stmt
            if a_stmt is None or a_stmt.empty:
                logger.info("[fin_report] yfinance: no data at all for %s", ticker)
                return []
            results = _extract_results(a_stmt, "年度财报摘要", "yfinance 年度财务数据",
                                        is_quarterly=False)
            logger.info("[fin_report] yfinance annual: %d periods for %s", len(results), ticker)
            return results

        except Exception as exc:
            logger.warning("[fin_report] yfinance failed for %s: %s", ticker, exc)
            return []

    # ── SEC EDGAR filing links ─────────────────────────────────────────────────

    def _ensure_edgar_cache(self) -> None:
        if self._edgar_cache_loaded:
            return
        try:
            resp = requests.get(_EDGAR_TICKERS_URL, headers=_EDGAR_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            for item in resp.json().values():
                FinancialReportService._edgar_cache[item["ticker"].upper()] = int(item["cik_str"])
            FinancialReportService._edgar_cache_loaded = True
            logger.info("[fin_report] EDGAR cache: %d tickers", len(self._edgar_cache))
        except Exception as exc:
            logger.warning("[fin_report] EDGAR cache failed: %s", exc)

    def _fetch_edgar(self, ticker: str, n_quarters: int = 1) -> list[dict]:
        self._ensure_edgar_cache()
        cik = self._edgar_cache.get(ticker.upper())
        if not cik:
            return []
        try:
            resp = requests.get(_EDGAR_SUBMISSIONS.format(cik), headers=_EDGAR_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("[fin_report] EDGAR submissions failed: %s", exc)
            return []

        recent = data.get("filings", {}).get("recent", {})
        forms, dates, accessions = (
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
        )
        cutoff = (date.today() - timedelta(days=730)).isoformat()
        results = []
        for form, filed, acc in zip(forms, dates, accessions):
            if form not in ("10-K", "10-Q"):
                continue
            if filed < cutoff:
                break
            acc_nodash = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{acc}-index.htm"
            results.append({
                "title": f"{form} 原文（{filed}）",
                "date": filed,
                "content": "",
                "url": url,
                "source": "SEC EDGAR",
            })
            if len(results) >= n_quarters:
                break
        return results

    # ── CNINFO filing links ────────────────────────────────────────────────────

    def _fetch_cninfo(self, stock_code: str, n_quarters: int = 1) -> list[dict]:
        payload = {
            "pageNum": 1, "pageSize": 10,
            "column": "szse", "tabName": "fulltext",
            "plate": "", "stock": stock_code, "searchkey": "",
            "category": "category_ndbg_szsh;category_bndbg_szsh;category_jb_szsh",
            "trade": "", "seDate": "",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "http://www.cninfo.com.cn/",
            "User-Agent": "Mozilla/5.0",
        }
        try:
            resp = requests.post(_CNINFO_URL, data=payload, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            items = (resp.json().get("announcements") or [])[:n_quarters]
        except Exception as exc:
            logger.warning("[fin_report] CNINFO failed: %s", exc)
            return []

        results = []
        for ann in items:
            adj = ann.get("adjunctUrl", "")
            ts  = ann.get("announcementTime", 0)
            filed = time.strftime("%Y-%m-%d", time.localtime(ts / 1000)) if ts else ""
            results.append({
                "title": ann.get("announcementTitle", ""),
                "date": filed,
                "content": "",
                "url": _CNINFO_PDF.format(adjunct_url=adj) if adj else "",
                "source": "巨潮资讯 CNINFO",
            })
        return results

    # ── HK: HKEXnews (no REST API, provide fixed search URLs) ────────────────
    # HKEXnews has no public REST API, but their search page accepts URL params.
    # FormType 13 = Annual Report / Results, 17 = Interim Report
    # StockCode must be zero-padded to 5 digits.

    def _fetch_hk_search(self, stock_code: str) -> list[dict]:
        # HKEXnews 搜索为 JavaScript 表单驱动，不支持 GET 参数过滤，无法构造有效链接。
        # 港股财报链接由 fetch() 末尾统一添加的 Yahoo Finance 财务页覆盖。
        return []
