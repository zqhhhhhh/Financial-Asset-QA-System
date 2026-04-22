"""
Ticker Resolver — 将用户自然语言查询解析为 Yahoo Finance 股票代码。

解析流程：
1. 显式 ticker 正则  — 用户已直接输入代码（如 AAPL、0700.HK）
2. LLM 直接返回     — LLM 具备全球股票知识，最通用
3. Alias 缓存表     — LLM 不可用时的静态快速缓存
4. yfinance Search  — 最后保障
"""

import logging
import re

import yfinance as yf

from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z]{1,3})?)\b")
_STOPWORDS = {"AND", "OR", "FOR", "IS", "IN", "AT", "BY", "TO", "THE", "OF", "A"}

_LLM_TICKER_PROMPT = (
    "你是全球资产代码专家。从用户问题中识别被询问的公司、指数、大宗商品或加密货币，"
    "返回其在 Yahoo Finance 上的代码。\n"
    "规则：\n"
    "- 美股：苹果→AAPL，可口可乐→KO，特斯拉→TSLA，英伟达→NVDA\n"
    "- 港股：腾讯→0700.HK，美团→3690.HK，小米→1810.HK\n"
    "- A股：茅台→600519.SS，比亚迪→002594.SZ\n"
    "- 中概股：阿里巴巴→BABA，百度→BIDU\n"
    "- 美股指数：纳斯达克/纳指→^IXIC，标普500/S&P500→^GSPC，道琼斯→^DJI\n"
    "- 其他指数：恒生指数→^HSI，上证→000001.SS，创业板→^CHINEXT\n"
    "- 大宗商品：黄金→GC=F，白银→SI=F，原油/石油→CL=F，天然气→NG=F，铜→HG=F\n"
    "- 加密货币：比特币/BTC→BTC-USD，以太坊/ETH→ETH-USD\n"
    "- ETF：纳斯达克100ETF→QQQ，标普500ETF→SPY，黄金ETF→GLD\n"
    "只返回代码本身，不要任何其他文字。\n"
    "如果问题中没有可识别的具体资产，返回空字符串。"
)

# LLM 不可用时的静态缓存
_ALIAS_CACHE: dict[str, str] = {
    # 美股
    "苹果": "AAPL", "apple": "AAPL",
    "特斯拉": "TSLA", "tesla": "TSLA",
    "微软": "MSFT", "microsoft": "MSFT",
    "亚马逊": "AMZN", "amazon": "AMZN",
    "谷歌": "GOOGL", "google": "GOOGL", "alphabet": "GOOGL",
    "meta": "META", "facebook": "META",
    "英伟达": "NVDA", "nvidia": "NVDA",
    "可口可乐": "KO", "coca cola": "KO", "coca-cola": "KO",
    "麦当劳": "MCD", "mcdonald": "MCD",
    # 中概股
    "阿里巴巴": "BABA", "阿里": "BABA", "alibaba": "BABA",
    "百度": "BIDU", "baidu": "BIDU",
    "京东": "JD", "拼多多": "PDD",
    "哔哩哔哩": "BILI", "b站": "BILI", "bilibili": "BILI",
    "蔚来": "NIO", "理想": "LI", "理想汽车": "LI", "小鹏": "XPEV",
    # 港股
    "腾讯": "0700.HK", "tencent": "0700.HK",
    "美团": "3690.HK", "小米": "1810.HK",
    "工商银行": "1398.HK", "平安": "2318.HK", "中国平安": "2318.HK",
    # A股
    "茅台": "600519.SS", "贵州茅台": "600519.SS",
    "比亚迪": "002594.SZ", "宁德时代": "300750.SZ",
    # 美股指数
    "纳斯达克": "^IXIC", "纳指": "^IXIC", "nasdaq": "^IXIC",
    "标普500": "^GSPC", "s&p500": "^GSPC", "s&p 500": "^GSPC",
    "道琼斯": "^DJI", "道指": "^DJI", "dow jones": "^DJI",
    # 港股/中国指数
    "恒生指数": "^HSI", "恒指": "^HSI", "上证": "000001.SS",
    # 大宗商品
    "黄金": "GC=F", "gold": "GC=F",
    "白银": "SI=F", "silver": "SI=F",
    "原油": "CL=F", "石油": "CL=F", "crude oil": "CL=F", "oil": "CL=F",
    "天然气": "NG=F", "natural gas": "NG=F",
    "铜": "HG=F", "copper": "HG=F",
    # 加密货币
    "比特币": "BTC-USD", "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "以太坊": "ETH-USD", "ethereum": "ETH-USD", "eth": "ETH-USD",
    # 热门 ETF
    "qqq": "QQQ", "spy": "SPY", "黄金etf": "GLD",
}

_PREFERRED_EXCHANGES = {"NYQ", "NMS", "NGM", "HKG", "SHH", "SHZ", "PCX", "ASE"}

# 验证 LLM 返回的 ticker 格式（支持指数 ^IXIC、期货 GC=F、加密 BTC-USD）
_VALID_TICKER_RE = re.compile(
    r"^(\^[A-Z0-9]{2,6}|[A-Z0-9]{1,6}(=[A-Z]|(-[A-Z]{3,4}))?(?:\.[A-Z]{1,3})?)$"
)


class TickerResolverService:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm = llm_service

    def resolve(self, query: str) -> str | None:
        # 1. 显式代码（用户直接输入）
        ticker = self._from_explicit_pattern(query)
        if ticker:
            logger.debug("[resolver] explicit → %s", ticker)
            return ticker

        # 2. LLM 直接返回（主路径）
        ticker = self._ask_llm(query)
        if ticker:
            logger.debug("[resolver] LLM → %s", ticker)
            return ticker

        # 3. 静态缓存（LLM 不可用时）
        ticker = self._from_alias(query)
        if ticker:
            logger.debug("[resolver] alias → %s", ticker)
            return ticker

        # 4. yfinance Search（最后保障）
        ticker = self._search_yfinance(query)
        if ticker:
            logger.debug("[resolver] yfinance search → %s", ticker)
            return ticker

        logger.warning("[resolver] cannot resolve: %s", query)
        return None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _from_explicit_pattern(self, query: str) -> str | None:
        for m in _TICKER_RE.finditer(query):
            candidate = m.group(1)
            if candidate not in _STOPWORDS:
                return candidate
        return None

    def _ask_llm(self, query: str) -> str | None:
        if not self.llm.available:
            return None
        raw = self.llm.chat(
            [
                {"role": "system", "content": _LLM_TICKER_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0,
            max_tokens=20,
            thinking=False,
        ).strip().upper()
        if raw and _VALID_TICKER_RE.match(raw):
            return raw
        return None

    def _from_alias(self, query: str) -> str | None:
        lowered = query.lower()
        for name in sorted(_ALIAS_CACHE, key=len, reverse=True):
            if name in lowered or name in query:
                return _ALIAS_CACHE[name]
        return None

    def _search_yfinance(self, name: str) -> str | None:
        try:
            results = yf.Search(name, news_count=0, max_results=8)
            quotes = results.quotes
            if not quotes:
                return None
            for q in quotes:
                if q.get("quoteType") == "EQUITY" and q.get("exchange") in _PREFERRED_EXCHANGES:
                    return q["symbol"]
            for q in quotes:
                if q.get("quoteType") == "EQUITY":
                    return q["symbol"]
            return quotes[0].get("symbol")
        except Exception as exc:
            logger.warning("[resolver] yfinance search failed: %s", exc)
            return None
