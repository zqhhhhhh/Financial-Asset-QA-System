import logging
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.services.financial_report_service import FinancialReportService, is_report_query
from app.services.llm_service import LLMService
from app.services.web_search_service import WebSearchService

logger = logging.getLogger(__name__)

_KB_SYSTEM = (
    "你是一位专业的金融知识助手，使用中文纯文本回答，禁止使用任何 Markdown 符号（不要 **、##、- 列表等）。\n"
    "直接进入正题，不要以「您好」或「根据您提供的」等套话开头。\n"
    "要求：\n"
    "1. 以检索到的参考内容为主要依据，结合你的金融专业知识进行补充和解读\n"
    "2. 关键数字和事实必须来自参考内容，不得凭空捏造具体数据\n"
    "3. 不要提及「知识库」「文档」「上下文」「您提供的」等系统内部术语\n"
    "4. 如果参考内容与问题完全无关，只回复固定文字：[KB_MISS]\n"
    "5. 回答完整清晰，不要截断"
)

_WEB_SEARCH_SYSTEM = (
    "你是一位专业的金融知识助手，使用中文纯文本回答，禁止使用任何 Markdown 符号（不要 **、##、- 列表等）。\n"
    "直接进入正题，不要以「您好」开头。\n"
    "以下内容来自实时网络搜索结果，请基于这些内容回答用户问题。\n"
    "要求：\n"
    "1. 以搜索结果为主要事实依据，结合金融专业知识进行解读，不得凭空捏造具体数字或事件\n"
    "2. 引用具体数据时说明来自哪个来源（网页标题即可），不要在正文输出 URL\n"
    "3. 如果搜索结果不足以回答，明确说明\n"
    "4. 回答完整清晰，不要截断\n"
    "5. 末尾注明「以上内容来自网络搜索，仅供参考」"
)


class RAGService:
    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(
        self,
        llm_service: LLMService,
        fin_report_service: FinancialReportService | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.web_search = WebSearchService()
        self.fin_report = fin_report_service
        logger.info("Loading embedding model: %s", self.EMBEDDING_MODEL)
        self.embedding_model = SentenceTransformer(self.EMBEDDING_MODEL)
        self.chunks: list[str] = []
        self.sources: list[str] = []
        self.index: faiss.IndexFlatIP | None = None
        self._build_index()

    def answer(self, query: str, history: list[dict] | None = None) -> tuple[str, dict]:
        # ── 1. 财报类查询：优先走专用 API（SEC EDGAR / CNINFO / HKEXnews）──
        # 必须在 KB 检索之前，否则 KB 的通用"财务报表"内容会干扰命中
        if self.fin_report and is_report_query(query):
            reports = self.fin_report.fetch(query)
            if reports:
                logger.info("[rag] financial report API hit: %d results", len(reports))
                return self._answer_from_reports(query, reports, history)
            # fetch() 返回空 = 未识别到具体公司 → 当成通用知识问题走 KB

        # ── 2. 知识库向量检索 ──────────────────────────────────────────────
        docs = self.retrieve(query, top_k=settings.rag_top_k)
        kb_hits = [d for d in docs if d["score"] >= settings.rag_score_threshold]

        if kb_hits:
            return self._answer_from_kb(query, kb_hits, history)

        # ── 3. 兜底：DuckDuckGo Web Search ───────────────────────────────
        logger.info("[rag] KB miss (top score=%.3f), web search", docs[0]["score"] if docs else 0)
        return self._answer_from_web(query, history)

    # ── 财报 API 回答 ──────────────────────────────────────────────────────

    def _answer_from_reports(
        self, query: str, reports: list[dict], history: list[dict] | None
    ) -> tuple[str, dict]:
        # 分离：有实际财务数据的条目 vs 仅含链接的条目
        data_items  = [r for r in reports if r.get("content")]
        link_items  = [r for r in reports if r.get("url") and not r.get("content")]

        context_parts = []
        for r in data_items:
            context_parts.append(f"【{r['title']}】\n{r['content']}")
        if link_items:
            context_parts.append(
                "【官方原文链接】\n" +
                "\n".join(f"- {r['title']}（{r['source']}）" for r in link_items)
            )
        context = "\n\n".join(context_parts) or "（未获取到具体财务数据）"

        system = (
            "你是一位专业的金融分析师，使用中文纯文本回答，禁止 Markdown 符号（不要 **、##、--- 等）。\n"
            "以下是该公司的财务数据（含同比/环比增速），请按以下三段结构输出财报摘要：\n\n"
            "【基本信息】\n"
            "公司名称 / 报告类型（季报/年报）/ 报告周期\n\n"
            "【核心财务指标】\n"
            "列出数据中所有可用指标及其同比/环比增速，数字必须与数据完全一致，禁止编造\n\n"
            "【分析】\n"
            "综合以下四点进行分析，内容连贯，不要分成四个子标题：\n"
            "亮点与变化（从数字提炼关键趋势）、风险因素（结合行业背景，不得捏造数据）、"
            "综合评估（增长质量、可持续性、市场地位）。\n"
            "末尾注明「以上分析仅供参考，完整财报原文链接见下方」\n\n"
            "严格禁止：\n"
            "- 禁止出现「您提供的」「根据您提供」「知识库」「文档」「上下文」等词\n"
            "- 禁止编造同比/环比数字，若数据中没有则不写增速\n"
            "- 禁止以「您好」开头，直接进入【基本信息】\n"
            "- 回答必须包含全部三个模块，不得截断"
        )
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[:-1])
        messages.append({"role": "user", "content": f"财务数据：\n{context}\n\n用户问题：{query}"})

        if self.llm_service.available:
            answer = self.llm_service.chat(messages, temperature=0.1, max_tokens=1500, thinking=False)
        else:
            answer = f"检索到以下财报文件：\n{context}"

        links = [{"title": r["title"], "url": r["url"]} for r in reports if r.get("url")]
        return answer, {"source": "financial_report_api", "docs": len(reports), "web_links": links}

    # ── 知识库回答 ─────────────────────────────────────────────────────────

    def _answer_from_kb(
        self, query: str, docs: list[dict], history: list[dict] | None
    ) -> tuple[str, dict]:
        context = "\n\n".join(
            f"[参考内容{i+1}]\n{d['text']}"
            for i, d in enumerate(docs)
        )
        messages = [{"role": "system", "content": _KB_SYSTEM}]
        if history:
            messages.extend(history[:-1])
        messages.append({"role": "user", "content": f"上下文：\n{context}\n\n问题：{query}"})

        if self.llm_service.available:
            answer = self.llm_service.chat(messages, temperature=0.1, max_tokens=800, thinking=False)
        else:
            answer = "\n".join(f"- [{d['source']}] {d['text'][:200]}…" for d in docs)

        # KB 内容与问题无关时，降级到 web search
        if "[KB_MISS]" in answer:
            logger.info("[rag] KB miss (LLM判断内容无关), fallback to web search")
            return self._answer_from_web(query, history)

        return answer, {"source": "knowledge_base", "docs": len(docs)}

    # ── Web Search 回答 ────────────────────────────────────────────────────

    def _answer_from_web(
        self, query: str, history: list[dict] | None
    ) -> tuple[str, dict]:
        # 用中文搜索，加上"金融"关键词提升结果质量
        search_query = f"{query} 金融"
        results = self.web_search.search(search_query, max_results=8)

        if not results:
            return (
                "知识库中未找到相关内容，网络搜索也未返回有效结果，建议查阅专业金融资料。",
                {"source": "none", "docs": 0},
            )

        context = "\n\n".join(
            f"[搜索结果{i+1} | 标题：{r['title']} | URL：{r['url']}]\n{r['snippet']}"
            for i, r in enumerate(results)
        )
        # 把 URL 列表单独传给前端展示
        web_links = [{"title": r["title"], "url": r["url"]} for r in results if r["url"]]

        messages = [{"role": "system", "content": _WEB_SEARCH_SYSTEM}]
        if history:
            messages.extend(history[:-1])
        messages.append({"role": "user", "content": f"搜索结果：\n{context}\n\n问题：{query}"})

        if self.llm_service.available:
            answer = self.llm_service.chat(messages, temperature=0.1, max_tokens=800, thinking=False)
        else:
            answer = "\n".join(f"- {r['title']}: {r['snippet'][:150]}…" for r in results[:3])

        return answer, {"source": "web_search", "docs": len(results), "web_links": web_links}

    # ── 向量检索 ───────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 4) -> list[dict]:
        if not self.index or not self.chunks:
            return []
        vector = self.embedding_model.encode(
            [query], normalize_embeddings=True
        ).astype("float32")
        scores, indices = self.index.search(vector, top_k)
        return [
            {"text": self.chunks[idx], "source": self.sources[idx], "score": float(score)}
            for score, idx in zip(scores[0], indices[0])
            if idx != -1
        ]

    def _build_index(self) -> None:
        knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "knowledge"
        files = sorted(
            list(knowledge_dir.glob("*.md")) + list(knowledge_dir.glob("*.txt"))
        )
        if not files:
            logger.warning("Knowledge base is empty: %s", knowledge_dir)
            return

        for file_path in files:
            content = file_path.read_text(encoding="utf-8")
            chunks = self._chunk_text(content, chunk_size=500, overlap=100)
            self.chunks.extend(chunks)
            self.sources.extend([file_path.name] * len(chunks))

        logger.info("Building FAISS index from %d chunks across %d files", len(self.chunks), len(files))
        embeddings = self.embedding_model.encode(
            self.chunks, normalize_embeddings=True, show_progress_bar=True
        ).astype("float32")
        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)
        logger.info("FAISS index ready (%d vectors, dim=%d)", len(self.chunks), embeddings.shape[1])

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end].strip())
            if end >= len(text):
                break
            start = end - overlap
        return [c for c in chunks if c]
