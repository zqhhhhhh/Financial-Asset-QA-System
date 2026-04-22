"""
从中文 Wikipedia 构建金融知识库。

支持两种抓取模式：
1. TOPICS 手动列表（精准控制）
2. CATEGORIES 分类自动发现（批量扩充）

用法（在 backend/ 目录下运行）：
    python scripts/build_knowledge.py            # 同时跑两种模式
    python scripts/build_knowledge.py --topics   # 仅手动列表
    python scripts/build_knowledge.py --cats     # 仅分类模式

每个词条保存为 data/knowledge/<slug>.md，内容来自 Wikipedia API，
不包含任何 AI 生成内容，确保事实准确性。
"""

import re
import sys
import time
from pathlib import Path

import requests

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge"
WIKI_API = "https://zh.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "FinancialQABot/1.0 (educational project)"}

# ── 手动精选词条 ──────────────────────────────────────────────────────────────
# (Wikipedia词条名, 保存文件的display标题)
TOPICS = [
    # 估值指标
    ("市盈率", "市盈率(P/E)"),
    ("市净率", "市净率(P/B)"),
    ("股息率", "股息率"),
    ("市销率", "市销率(P/S)"),
    # 财务报表与指标
    ("財務報表", "财务报表"),
    ("損益表", "损益表(利润表)"),
    ("資產負債表", "资产负债表"),
    ("現金流量表", "现金流量表"),
    ("营业额", "营业收入"),
    ("净利润", "净利润"),
    ("毛利率", "毛利率"),
    ("息税前利润", "EBIT(息税前利润)"),
    ("每股盈利", "每股收益(EPS)"),
    ("自由现金流", "自由现金流(FCF)"),
    ("净资产收益率", "净资产收益率(ROE)"),
    ("资产负债率", "资产负债率"),
    ("流动比率", "流动比率"),
    ("速动比率", "速动比率"),
    # 股票基础
    ("股票", "股票"),
    ("普通股", "普通股"),
    ("優先股", "优先股"),
    ("股息", "股息"),
    ("股票回購", "股票回购"),
    ("首次公開募股", "首次公开募股(IPO)"),
    ("卖空", "做空(卖空)"),
    ("孖展", "融资融券(保证金交易)"),
    ("配股", "配股"),
    # 投资工具
    ("債券", "债券"),
    ("基金", "基金"),
    ("交易所交易基金", "ETF(交易所交易基金)"),
    ("期貨", "期货"),
    ("期權", "期权"),
    ("可轉換債券", "可转换债券"),
    ("衍生工具", "金融衍生品"),
    ("权证", "权证"),
    ("指数基金", "指数基金"),
    ("对冲基金", "对冲基金"),
    ("私募股權", "私募股权(PE)"),
    ("风险投资", "风险投资(VC)"),
    # 市场与指数
    ("牛市", "牛市"),
    ("熊市", "熊市"),
    ("市值", "市值(市场资本化)"),
    ("流通股", "流通股"),
    ("标准普尔500指数", "标准普尔500指数(S&P 500)"),
    ("道琼斯工业平均指数", "道琼斯工业平均指数"),
    ("恒生指数", "恒生指数"),
    ("上证指数", "上证综合指数"),
    ("市场流动性", "市场流动性"),
    ("VIX指数", "VIX波动率指数"),
    # 分析方法
    ("基本面分析", "基本面分析"),
    ("技術分析", "技术分析"),
    ("股本回報率", "ROE(股本回报率)"),
    ("資產回報率", "ROA(资产回报率)"),
    ("杜邦分析法", "杜邦分析法"),
    ("价值投资", "价值投资"),
    ("資產配置", "资产配置"),
    ("投資組合", "投资组合理论"),
    ("夏普比率", "夏普比率"),
    ("贝塔系数", "贝塔系数(Beta)"),
    ("阿爾法", "阿尔法收益(Alpha)"),
    # 宏观经济
    ("通货膨胀", "通货膨胀"),
    ("通货紧缩", "通货紧缩"),
    ("利率", "利率"),
    ("量化寬鬆", "量化宽松(QE)"),
    ("联邦基金利率", "联邦基金利率"),
    ("货币政策", "货币政策"),
    ("财政政策", "财政政策"),
    ("国内生产总值", "GDP(国内生产总值)"),
    ("失业率", "失业率"),
    ("消費者物價指數", "CPI(消费者物价指数)"),
    ("生產者物價指數", "PPI(生产者物价指数)"),
    ("汇率", "汇率"),
    ("美元指数", "美元指数"),
    ("经济周期", "经济周期"),
    ("经济衰退", "经济衰退"),
    ("收益率曲线", "收益率曲线"),
    # 加密货币
    ("比特币", "比特币(Bitcoin)"),
    ("以太坊", "以太坊(Ethereum)"),
    ("区块链", "区块链技术"),
    ("数字货币", "数字货币/央行数字货币"),
    # 行业
    ("半导体", "半导体行业"),
    ("人工智能", "人工智能(AI)"),
    ("新能源汽车", "新能源汽车行业"),
    ("电子商务", "电子商务行业"),
    ("云计算", "云计算行业"),
    # 风险管理
    ("系統性風險", "系统性风险"),
    ("套期保值", "对冲(套期保值)"),
    ("分散投資", "投资多元化"),
    # 交易所与监管
    ("纽约证券交易所", "纽约证券交易所(NYSE)"),
    ("纳斯达克", "纳斯达克交易所"),
    ("香港交易所", "香港交易所(HKEX)"),
    ("上海证券交易所", "上海证券交易所(SSE)"),
    ("深圳证券交易所", "深圳证券交易所(SZSE)"),
    # 大宗商品
    ("黄金", "黄金投资"),
    ("石油", "石油(原油)"),
    ("铜", "铜"),
]

# ── 分类自动发现 ──────────────────────────────────────────────────────────────
# 中文 Wikipedia 金融相关分类，脚本会自动拉取每个分类下的所有词条
CATEGORIES = [
    "Category:金融市场",
    "Category:证券",
    "Category:投资",
    "Category:债券",
    "Category:股票市场",
    "Category:衍生金融工具",
    "Category:货币政策",
    "Category:宏观经济学",
    "Category:公司金融",
    "Category:财务报表",
    "Category:会计",
    "Category:银行业",
    "Category:保险",
    "Category:加密货币",
    "Category:股票指数",
    "Category:证券交易所",
    "Category:风险管理",
    "Category:投资策略",
]

# 最短内容长度（过滤太短的词条）
MIN_CONTENT_LEN = 200
# 单词条最大保存长度（避免极长文章占用太多 token）
MAX_CONTENT_LEN = 4000
# 每个分类最多抓取多少词条（防止过大）
CATEGORY_MAX_PER_CAT = 60


# ── Wikipedia API helpers ─────────────────────────────────────────────────────

def fetch_category_members(category: str, limit: int = 500) -> list[str]:
    """返回分类下所有主命名空间（ns=0）词条的标题列表。"""
    titles = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmnamespace": 0,   # 只要正文词条，排除子分类
        "cmlimit": min(limit, 500),
        "format": "json",
    }
    while True:
        try:
            resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  [error] category query failed: {exc}")
            break
        for member in data.get("query", {}).get("categorymembers", []):
            titles.append(member["title"])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont or len(titles) >= limit:
            break
        params["cmcontinue"] = cont
        time.sleep(0.3)
    return titles[:limit]


def fetch_wiki(title: str) -> tuple[str, str] | tuple[None, None]:
    """获取 Wikipedia 词条全文（纯文本），自动跟随重定向。"""
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "extracts",
        "explaintext": True,
        "exsectionformat": "plain",
        "redirects": 1,
    }
    try:
        resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pages = data["query"]["pages"]
        page = next(iter(pages.values()))
        if "missing" in page:
            return None, None
        actual_title = page.get("title", title)
        content = page.get("extract", "")
        if "重定向" in content[:50] and len(content) < 300:
            return None, None
        return actual_title, content
    except Exception as exc:
        print(f"  [error] fetch failed: {exc}")
        return None, None


def clean_text(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("\xa0", " ")
    return text.strip()


def title_to_slug(title: str) -> str:
    return re.sub(r"[^\w\-]", "_", title)


def save(slug: str, title: str, content: str) -> None:
    path = OUTPUT_DIR / f"{slug}.md"
    path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    print(f"  [saved] {path.name} ({len(content)} chars)")


# ── 抓取逻辑 ──────────────────────────────────────────────────────────────────

def fetch_and_save(wiki_title: str, display_title: str, existing_slugs: set[str]) -> bool:
    """抓取单个词条，成功返回 True。"""
    slug = title_to_slug(wiki_title)
    if slug in existing_slugs:
        return False  # 已存在，跳过

    actual_title, text = fetch_wiki(wiki_title)
    if not text:
        return False

    cleaned = clean_text(text)
    if len(cleaned) < MIN_CONTENT_LEN:
        return False

    if len(cleaned) > MAX_CONTENT_LEN:
        cleaned = cleaned[:MAX_CONTENT_LEN] + "\n\n（内容节选自中文维基百科）"

    save(slug, display_title or actual_title, cleaned)
    existing_slugs.add(slug)
    return True


def run_topics(existing_slugs: set[str]) -> int:
    print(f"\n── 手动精选词条（{len(TOPICS)} 条）──\n")
    success = 0
    for wiki_title, display_title in TOPICS:
        print(f"Fetching: {wiki_title}")
        if fetch_and_save(wiki_title, display_title, existing_slugs):
            success += 1
        time.sleep(0.8)
    return success


def run_categories(existing_slugs: set[str]) -> int:
    print(f"\n── 分类自动发现（{len(CATEGORIES)} 个分类）──\n")
    total_success = 0

    for cat in CATEGORIES:
        print(f"\n[分类] {cat}")
        members = fetch_category_members(cat, limit=CATEGORY_MAX_PER_CAT)
        print(f"  发现 {len(members)} 个词条")
        cat_success = 0
        for title in members:
            slug = title_to_slug(title)
            if slug in existing_slugs:
                continue  # 手动列表已抓，跳过
            print(f"  Fetching: {title}")
            if fetch_and_save(title, title, existing_slugs):
                cat_success += 1
                total_success += 1
            time.sleep(0.6)
        print(f"  [分类完成] 新增 {cat_success} 条")

    return total_success


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode not in ("--topics", "--cats", "all"):
        print("用法: python scripts/build_knowledge.py [--topics|--cats]")
        sys.exit(1)

    # 清理旧文件（仅在全量重建时清理）
    if mode == "all":
        for old in OUTPUT_DIR.glob("*.md"):
            old.unlink()
            print(f"[removed] {old.name}")

    existing_slugs: set[str] = {f.stem for f in OUTPUT_DIR.glob("*.md")}

    topic_count = cat_count = 0

    if mode in ("all", "--topics"):
        topic_count = run_topics(existing_slugs)

    if mode in ("all", "--cats"):
        cat_count = run_categories(existing_slugs)

    total = topic_count + cat_count
    print(f"\nDone: {total} topics saved to {OUTPUT_DIR}")
    print(f"  手动列表: {topic_count}, 分类自动: {cat_count}")
    print(f"  知识库总文件数: {len(list(OUTPUT_DIR.glob('*.md')))}")


if __name__ == "__main__":
    main()
