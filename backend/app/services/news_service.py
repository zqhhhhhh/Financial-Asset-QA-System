"""Finnhub-based news service with date-range support."""
import logging
from datetime import date, datetime, timedelta

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 8  # seconds

# 期货/指数/加密货币没有 Finnhub company-news，用对应 ETF 代理查新闻
_FINNHUB_PROXY: dict[str, str] = {
    # 大宗商品 → ETF
    "GC=F": "GLD",    # 黄金 → SPDR Gold ETF
    "SI=F": "SLV",    # 白银 → iShares Silver Trust
    "CL=F": "USO",    # 原油 → United States Oil Fund
    "NG=F": "UNG",    # 天然气 → United States Natural Gas Fund
    "HG=F": "COPX",   # 铜 → Global X Copper Miners
    # 加密货币 → 相关股票（Coinbase 流动性最好）
    "BTC-USD": "COIN",
    "ETH-USD": "COIN",
    # 美股指数 → 对应 ETF
    "^IXIC": "QQQ",
    "^GSPC": "SPY",
    "^DJI": "DIA",
    "^HSI": "EWH",
    "^VIX": "VIXY",
}


def _to_finnhub_ticker(ticker: str) -> str:
    """Convert Yahoo-format ticker to Finnhub format."""
    # 期货/指数/加密：用代理 ETF
    if ticker in _FINNHUB_PROXY:
        return _FINNHUB_PROXY[ticker]
    # HK stocks: 0700.HK → 700:HKG
    if ticker.endswith(".HK"):
        code = ticker[:-3].lstrip("0") or "0"
        return f"{code}:HKG"
    # A-shares: Finnhub 不可靠，跳过
    if ticker.endswith(".SS") or ticker.endswith(".SZ"):
        return ""
    # 其他特殊格式（=F、-USD、^ 未在 proxy 中）：跳过
    if any(c in ticker for c in ("=", "-", "^")):
        return ""
    return ticker


class NewsService:
    """Fetch date-ranged news via Finnhub API, with yfinance fallback."""

    @property
    def available(self) -> bool:
        return bool(settings.finnhub_api_key)

    def fetch(
        self,
        yf_ticker_obj,          # yfinance Ticker, used for fallback
        ticker: str,            # Yahoo-format ticker string
        start: date,
        end: date,
        max_items: int = 200,
    ) -> list[dict]:
        """Return news dicts with keys: title, url, publisher, date, summary."""
        if self.available:
            news = self._fetch_finnhub(ticker, start, end, max_items)
            if news:
                logger.info(
                    "[news] Finnhub: %d items for %s (%s~%s)",
                    len(news), ticker, start, end,
                )
                return news
            logger.info("[news] Finnhub returned 0 items for %s, falling back to yfinance", ticker)

        items = self._fetch_yfinance(yf_ticker_obj, max_items)
        logger.info("[news] yfinance fallback: %d items for %s", len(items), ticker)
        return items

    # ── Finnhub ───────────────────────────────────────────────────────────────

    def _fetch_finnhub(
        self, ticker: str, start: date, end: date, max_items: int
    ) -> list[dict]:
        fh_ticker = _to_finnhub_ticker(ticker)
        if not fh_ticker:
            return []

        # 日期窗口：向前多拉 3 天，避免周末/节假日边界遗漏
        from_str = (start - timedelta(days=3)).isoformat()
        to_str = end.isoformat()

        url = (
            f"{_FINNHUB_BASE}/company-news"
            f"?symbol={fh_ticker}"
            f"&from={from_str}&to={to_str}"
            f"&token={settings.finnhub_api_key}"
        )
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            items = resp.json()
        except Exception as exc:
            logger.warning("[news] Finnhub request failed for %s: %s", ticker, exc)
            return []

        if not isinstance(items, list):
            return []

        news = []
        for item in items[:max_items]:
            headline = item.get("headline", "").strip()
            if not headline:
                continue
            ts = item.get("datetime")
            pub_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
            news.append({
                "title": headline,
                "url": item.get("url", ""),
                "publisher": item.get("source", ""),
                "date": pub_date,
                "summary": item.get("summary", ""),
            })

        return news

    # ── yfinance fallback ─────────────────────────────────────────────────────

    def _fetch_yfinance(self, tkr, max_items: int) -> list[dict]:
        try:
            raw_news = tkr.news or []
            news = []
            for item in raw_news[:max_items]:
                content = item.get("content", item)
                title = content.get("title") or item.get("title", "")
                url = (
                    content.get("canonicalUrl", {}).get("url")
                    or content.get("url")
                    or item.get("link", "")
                )
                pub_time = content.get("pubDate") or item.get("providerPublishTime")
                if isinstance(pub_time, (int, float)):
                    pub_time = datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d")
                publisher = (
                    content.get("provider", {}).get("displayName")
                    if isinstance(content.get("provider"), dict)
                    else item.get("publisher", "")
                )
                if title:
                    news.append({
                        "title": title,
                        "url": url,
                        "publisher": publisher,
                        "date": pub_time or "",
                        "summary": "",
                    })
            return news
        except Exception as exc:
            logger.warning("[news] yfinance fetch failed: %s", exc)
            return []
