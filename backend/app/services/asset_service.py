import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import yfinance as yf

from app.services.llm_service import LLMService
from app.services.news_service import NewsService
from app.services.ticker_resolver import TickerResolverService

logger = logging.getLogger(__name__)

# ── LLM prompts ───────────────────────────────────────────────────────────────

def _build_period_prompt() -> str:
    today = date.today().isoformat()
    return f"""今天的日期是 {today}。
从用户问题中提取查询的时间范围，返回一个 JSON 对象，格式如下：

{{"days": <整数天数>}}         # 最近 N 天，例如 7天→{{"days":7}}，今天→{{"days":1}}
或
{{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}  # 特定日期区间

规则：
- 今天 / 今日 / 当前 / 最新 / 实时 / 现在 → {{"days": 1}}
- 近 N 天 / 最近 N 天 → {{"days": N}}
- 本周 / 一周 / 7天 → {{"days": 7}}
- 一个月 / 30天 → {{"days": 30}}
- 季度 / 3个月 → {{"days": 90}}
- 半年 / 6个月 → {{"days": 180}}
- 一年 → {{"days": 365}}
- 用户提到具体历史日期（如"1月15日"）→ 返回该日期前后各3天的区间，无年份默认当前年
- 无明确时间 → {{"days": 7}}

只返回 JSON，不要任何其他内容。"""

_ANSWER_SYSTEM = (
    "你是一位专业的金融分析师，使用中文纯文本回答，禁止使用任何 Markdown 符号（不要 **、##、- 列表等）。\n"
    "市场数据和新闻由系统自动获取，无需向用户提及数据来源。\n"
    "回答要求：\n"
    "1. 直接进入正题，不要以「您好」或「根据您/根据提供的」等套话开头\n"
    "2. 用【事实】和【分析】两个段落组织回答\n"
    "3. 【事实】只陈述市场数据中的客观信息，数字必须与数据完全一致，不得编造\n"
    "4. 【分析】发挥你的金融专业判断，自由分析走势原因、市场背景、行业逻辑等，"
    "新闻仅作为背景参考，不必逐条引用，末尾注明「以上分析仅供参考」\n"
    "5. 不要在正文中输出新闻链接 URL（链接由系统单独展示）\n"
    "6. 不得捏造具体事件\n"
    "7. 回答完整，不要截断"
)


@dataclass
class DateRange:
    start: date
    end: date
    label: str
    is_today: bool = False  # 是否是"今天"查询（用于切换分钟级数据）


@dataclass
class IntradayData:
    open_price: float
    current_price: float
    high: float
    low: float
    change_from_open_pct: float
    prev_close: float
    change_from_prev_pct: float
    data_points: int  # 分钟级数据点数


@dataclass
class AssetResult:
    ticker: str
    company_name: str
    latest_price: float
    change_pct: float
    trend: str
    date_range: DateRange
    currency: str = "USD"
    news: list[dict] = field(default_factory=list)       # 全量新闻，喂给 LLM
    top_news: list[dict] = field(default_factory=list)   # 相关度 top5，展示给用户
    intraday: IntradayData | None = None
    yahoo_url: str = ""
    robinhood_url: str = ""

    def to_dict(self) -> dict:
        sign = "+" if self.change_pct >= 0 else ""
        d: dict = {
            "ticker": self.ticker,
            "company": self.company_name,
            "price": round(self.latest_price, 2),
            "currency": self.currency,
            "change": f"{sign}{self.change_pct:.2f}%",
            "trend": self.trend,
            "period": self.date_range.label,
            "yahoo_url": self.yahoo_url,
        }
        if self.robinhood_url:
            d["robinhood_url"] = self.robinhood_url
        if self.top_news:
            d["news"] = [
                {"title": n["title"], "url": n.get("url", ""), "date": n.get("date", "")}
                for n in self.top_news if n.get("title")
            ]
        if self.intraday:
            d["intraday"] = {
                "open": self.intraday.open_price,
                "high": self.intraday.high,
                "low": self.intraday.low,
                "change_from_open": f"{'+' if self.intraday.change_from_open_pct >= 0 else ''}{self.intraday.change_from_open_pct:.2f}%",
                "change_from_prev_close": f"{'+' if self.intraday.change_from_prev_pct >= 0 else ''}{self.intraday.change_from_prev_pct:.2f}%",
                "data_points": self.intraday.data_points,
            }
        return d


class AssetService:
    def __init__(
        self,
        llm_service: LLMService,
        ticker_resolver: TickerResolverService,
        news_service: NewsService | None = None,
    ) -> None:
        self.llm = llm_service
        self.ticker_resolver = ticker_resolver
        self.news_svc = news_service or NewsService()

    def answer(self, query: str, history: list[dict] | None = None) -> tuple[str, dict]:
        # ── 1. 解析 ticker ──────────────────────────────────────────────────
        ticker = self.ticker_resolver.resolve(query)
        if ticker is None:
            return self._general_market_analysis(query, history)

        # ── 2. 解析时间段 ────────────────────────────────────────────────
        date_range = self._extract_date_range(query)

        # ── 3. 拉取市场数据 ───────────────────────────────────────────────
        try:
            result = self._fetch_asset_data(ticker, date_range)
        except ValueError as exc:
            return str(exc), {"error": str(exc), "ticker": ticker}

        payload = result.to_dict()

        # ── 4. 构建新闻上下文（标注时效性） ──────────────────────────────
        logger.info(
            "[asset] ticker=%s range=%s total_news=%d top_news=%d",
            ticker, date_range.label, len(result.news), len(result.top_news),
        )
        news_context = self._format_news_with_context(result.news, date_range)

        # ── 5. 构建 LLM 提示 ──────────────────────────────────────────────
        messages = [{"role": "system", "content": _ANSWER_SYSTEM}]
        if history:
            messages.extend(history[:-1])
        messages.append({
            "role": "user",
            "content": (
                f"【市场数据】\n{payload}\n\n"
                f"【相关新闻】\n{news_context}\n\n"
                f"【用户问题】\n{query}"
            ),
        })

        if self.llm.available:
            answer = self.llm.chat(messages, temperature=0.15, max_tokens=2000, thinking=False)
        else:
            d = payload
            answer = (
                f"【事实】\n"
                f"- {d['company']}（{d['ticker']}）\n"
                f"- 最新价格：{d['price']} {d['currency']}\n"
                f"- {d['period']} 涨跌幅：{d['change']}\n"
                f"- 趋势：{d['trend']}\n\n"
                f"【分析】\n- 当前 LLM 不可用，仅展示原始数据。"
            )

        return answer, payload

    def _general_market_analysis(
        self, query: str, history: list[dict] | None
    ) -> tuple[str, dict]:
        """无法识别具体 ticker 时，让 LLM 基于自身知识做市场分析。"""
        _GENERAL_SYSTEM = (
            "你是一位专业的金融分析师，使用中文纯文本回答，禁止使用任何 Markdown 符号。\n"
            "直接进入正题，不要以「您好」开头。\n"
            "用户询问的是宏观市场、行业或大宗商品走势等一般性问题。\n"
            "请结合你的金融知识做出分析，末尾注明「以上分析仅供参考」。\n"
            "如涉及具体价格或近期数据，请说明你的知识存在截止日期，建议用户查阅实时行情。"
        )
        if not self.llm.available:
            return "当前无法识别具体资产代码，LLM 也不可用，请提供更明确的股票代码或公司名称。", {}

        messages = [{"role": "system", "content": _GENERAL_SYSTEM}]
        if history:
            messages.extend(history[:-1])
        messages.append({"role": "user", "content": query})

        answer = self.llm.chat(messages, temperature=0.3, max_tokens=1000, thinking=False)
        return answer, {}

    # ── date range extraction ─────────────────────────────────────────────────

    def _extract_date_range(self, query: str) -> DateRange:
        today = date.today()

        if self.llm.available:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": _build_period_prompt()},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=80,
                thinking=False,
            ).strip()
            m = re.search(r"\{.*?\}", raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group())
                    if "days" in obj:
                        days = max(1, min(int(obj["days"]), 365 * 5))
                        is_today = (days == 1)
                        start = today if is_today else today - timedelta(days=days - 1)
                        label = "今天" if is_today else f"最近{days}天"
                        return DateRange(start=start, end=today, label=label, is_today=is_today)
                    if "start" in obj and "end" in obj:
                        start = date.fromisoformat(obj["start"])
                        end = date.fromisoformat(obj["end"])
                        return DateRange(
                            start=start, end=end,
                            label=f"{start.isoformat()} 至 {end.isoformat()}",
                        )
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass

        # 关键词兜底
        if any(k in query for k in ["今天", "今日", "当前", "最新", "实时", "现在"]):
            return DateRange(today, today, "今天", is_today=True)
        if any(k in query for k in ["一个月", "30天", "月线"]):
            return DateRange(today - timedelta(days=29), today, "最近30天")
        if any(k in query for k in ["季度", "3个月"]):
            return DateRange(today - timedelta(days=89), today, "最近90天")
        if any(k in query for k in ["半年", "6个月"]):
            return DateRange(today - timedelta(days=179), today, "最近180天")
        if any(k in query for k in ["一年", "年线"]):
            return DateRange(today - timedelta(days=364), today, "最近一年")
        return DateRange(today - timedelta(days=6), today, "最近7天")

    # ── data fetching ─────────────────────────────────────────────────────────

    def _fetch_asset_data(self, ticker: str, date_range: DateRange) -> AssetResult:
        tkr = yf.Ticker(ticker)

        currency = "HKD" if ticker.endswith(".HK") else (
            "CNY" if ticker.endswith((".SS", ".SZ")) else "USD"
        )

        try:
            company_name = getattr(tkr.fast_info, "display_name", None) or ticker
        except Exception:
            company_name = ticker

        # ── 生成链接 ────────────────────────────────────────────────────
        yahoo_url = f"https://finance.yahoo.com/quote/{ticker}"
        # Robinhood 仅支持普通美股（无 . / ^ / = / - 等特殊字符）
        is_us_equity = (
            not any(c in ticker for c in (".", "^", "=", "-"))
        )
        robinhood_url = f"https://robinhood.com/stocks/{ticker}" if is_us_equity else ""

        intraday: IntradayData | None = None

        # ── 今日实时行情：分钟级数据 ─────────────────────────────────────
        if date_range.is_today:
            intraday_hist = tkr.history(period="1d", interval="1m")
            if intraday_hist.empty:
                raise ValueError(f"无法获取 {ticker} 今日实时行情（可能尚未开盘或非交易日）。")

            open_price = float(intraday_hist["Open"].iloc[0])
            current_price = float(intraday_hist["Close"].iloc[-1])
            high = float(intraday_hist["High"].max())
            low = float(intraday_hist["Low"].min())
            change_from_open = (current_price - open_price) / open_price * 100

            # 对比前一交易日收盘价
            prev_hist = tkr.history(period="5d", interval="1d")
            prev_closes = prev_hist["Close"].dropna().values
            if len(prev_closes) >= 2:
                prev_close = float(prev_closes[-2])
                change_from_prev = (current_price - prev_close) / prev_close * 100
            else:
                prev_close = open_price
                change_from_prev = change_from_open

            intraday = IntradayData(
                open_price=round(open_price, 2),
                current_price=round(current_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                change_from_open_pct=change_from_open,
                prev_close=round(prev_close, 2),
                change_from_prev_pct=change_from_prev,
                data_points=len(intraday_hist),
            )

            all_news = self.news_svc.fetch(tkr, ticker, date_range.start, date_range.end, max_items=200)
            return AssetResult(
                ticker=ticker,
                company_name=company_name,
                latest_price=current_price,
                change_pct=change_from_prev,
                trend="实时行情",
                date_range=date_range,
                currency=currency,
                news=all_news,
                top_news=self._rank_news(all_news, ticker, company_name, date_range, top_k=5),
                intraday=intraday,
                yahoo_url=yahoo_url,
                robinhood_url=robinhood_url,
            )

        # ── 历史区间：日线数据 ───────────────────────────────────────────
        fetch_start = date_range.start - timedelta(days=10)
        end_str = (date_range.end + timedelta(days=1)).isoformat()
        history = tkr.history(start=fetch_start.isoformat(), end=end_str, interval="1d")

        if history.empty:
            raise ValueError(
                f"无法获取 {ticker} 在 {date_range.start} ~ {date_range.end} 的数据，"
                "请确认股票代码和日期是否正确（非交易日无数据）。"
            )

        all_closes = history["Close"].dropna()
        range_mask = (
            (all_closes.index.date >= date_range.start)
            & (all_closes.index.date <= date_range.end)
        )
        range_closes = all_closes[range_mask].values
        prev_closes = all_closes[~range_mask].values  # 区间开始前的收盘价序列

        if len(range_closes) >= 1:
            closes = range_closes
            # 以区间前一个交易日收盘价为基准（与 Robinhood/Yahoo 1W 计算一致）
            ref_price = float(prev_closes[-1]) if len(prev_closes) > 0 else float(range_closes[0])
            change_pct = float((range_closes[-1] - ref_price) / ref_price * 100)
            trend_prices = np.concatenate([[ref_price], closes])
        else:
            closes = all_closes.values
            change_pct = float((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) >= 2 else 0.0
            trend_prices = closes

        latest_price = float(closes[-1])
        if len(trend_prices) >= 3:
            x = np.arange(len(trend_prices))
            slope = float(np.polyfit(x, trend_prices, 1)[0])
            norm = slope / (float(np.mean(trend_prices)) or 1.0)
            trend = "上升趋势" if norm > 0.002 else ("下降趋势" if norm < -0.002 else "横盘震荡")
        else:
            trend = "数据点不足"

        all_news = self.news_svc.fetch(tkr, ticker, date_range.start, date_range.end, max_items=200)
        return AssetResult(
            ticker=ticker,
            company_name=company_name,
            latest_price=latest_price,
            change_pct=change_pct,
            trend=trend,
            date_range=date_range,
            currency=currency,
            news=all_news,
            top_news=self._rank_news(all_news, ticker, company_name, date_range, top_k=5),
            yahoo_url=yahoo_url,
            robinhood_url=robinhood_url,
        )

    def _rank_news(
        self,
        news: list[dict],
        ticker: str,
        company_name: str,
        date_range: DateRange,
        top_k: int = 5,
    ) -> list[dict]:
        """按相关度打分，返回 top_k 条新闻用于前端展示。"""
        if not news:
            return []

        ticker_base = ticker.split(".")[0].lower()
        # 公司名拆词（过滤单字/英文停用词）
        company_keywords = [
            w.lower() for w in company_name.replace("公司", "").replace("集团", "").split()
            if len(w) > 1
        ]

        def score(n: dict) -> float:
            s = 0.0
            title = n.get("title", "").lower()
            # ticker 出现在标题
            if ticker_base in title:
                s += 3
            # 公司名关键词出现在标题
            if any(kw in title for kw in company_keywords):
                s += 2
            # 日期与查询区间的接近度
            raw_date = n.get("date", "")
            if raw_date:
                try:
                    news_date = date.fromisoformat(raw_date[:10])
                    if date_range.start <= news_date <= date_range.end:
                        s += 4  # 在查询区间内最优先
                    else:
                        # 离区间越近得分越高，最多 +2
                        dist = min(
                            abs((news_date - date_range.start).days),
                            abs((news_date - date_range.end).days),
                        )
                        s += max(0.0, 2.0 - dist / 7)
                except ValueError:
                    pass
            return s

        ranked = sorted(news, key=score, reverse=True)
        return ranked[:top_k]

    def _format_news_with_context(self, news: list[dict], date_range: DateRange) -> str:
        today = date.today()
        # 判断查询时间是否为历史（超过 14 天前）
        is_historical = date_range.end < today - timedelta(days=14)

        header = ""
        if is_historical:
            header = (
                f"⚠️ 注意：以下新闻为当前最新新闻（截至 {today.isoformat()}），"
                f"不覆盖查询区间（{date_range.label}）。"
                f"如需分析该历史区间内的涨跌原因，请勿基于以下新闻推断，"
                f"只能基于市场数据和明确已公开的历史事件（需标注「基于历史记录」）。\n"
            )

        if not news:
            return header + "暂无相关新闻。"

        lines = [header] if header else []
        for n in news:
            date_str = f"[{n['date']}] " if n.get("date") else ""
            src = f"（{n['publisher']}）" if n.get("publisher") else ""
            url_str = f" {n['url']}" if n.get("url") else ""
            lines.append(f"• {date_str}{n['title']}{src}{url_str}")

        return "\n".join(lines)
