import re

from app.services.llm_service import LLMService

SYSTEM_PROMPT = (
    "你是一个意图分类器。将用户的问题分类为以下三类之一：\n"
    "1. 资产行情 / 市场数据与分析：询问某具体资产的股价、涨跌、走势，或对其进行投资分析\n"
    "   例如：'苹果今天股价'、'阿里巴巴最近7天走势'、'黄金值得入股吗'、'纳斯达克最近表现如何'、'分析一下特斯拉的走势' → 1\n"
    "2. 金融知识：询问金融概念、术语解释、行业知识、公司/市场构成、财报摘要、估值分析等\n"
    "   例如：'什么是市盈率'、'英伟达最近季度财报'、'收入和净利润的区别'、'如何分析财报'、\n"
    "   '标普500包含哪些行业'、'什么是量化宽松'、'巴菲特的投资理念' → 2\n"
    "3. 一般聊天 / 不相关：问候、笑话、非金融话题\n"
    "   例如：'你好'、'讲个笑话'、'今天天气怎么样' → 3\n"
    "判断规则：只要问题与金融、股票、经济、投资、公司相关，优先选 1 或 2，不要选 3。\n"
    "只返回数字 1、2 或 3，不要有其他内容。"
)

ASSET_KEYWORDS = [
    "stock", "ticker", "股价", "涨", "跌", "行情", "走势", "k线",
    "市值", "成交量", "换手率", "最高", "最低",
]
KNOWLEDGE_KEYWORDS = [
    "what is", "是什么", "定义", "区别", "difference", "如何分析",
    "怎么看", "怎么理解", "原理", "概念", "估值",
    "市盈率", "市净率", "pe", "pb", "roe", "roa",
    "财报", "季报", "年报", "营收", "净利润", "eps", "每股收益", "毛利率",
]

ASSET_KEYWORDS = [
    "price", "trend", "stock", "ticker", "股价", "涨", "跌", "行情",
    "市值", "走势", "k线", "成交量", "换手率", "最高", "最低",
]
KNOWLEDGE_KEYWORDS = [
    "what is", "是什么", "定义", "区别", "difference", "pe", "市盈率",
    "财报", "净利润", "收入", "如何分析", "怎么看", "估值", "市净率",
    "毛利率", "roe", "资产负债", "现金流", "利润表",
]


class RouterService:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def classify(self, query: str) -> int:
        if self.llm_service.available:
            result = self.llm_service.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=10,
                thinking=False,
            )
            match = re.search(r"[123]", result)
            if match:
                return int(match.group(0))

        # Keyword fallback when LLM is unavailable
        lowered = query.lower()
        if any(w in lowered for w in ASSET_KEYWORDS):
            return 1
        if any(w in lowered for w in KNOWLEDGE_KEYWORDS):
            return 2
        return 3
