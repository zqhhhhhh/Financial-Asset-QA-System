from app.services.asset_service import AssetService
from app.services.financial_report_service import FinancialReportService
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.router_service import RouterService
from app.services.session_service import SessionService
from app.services.ticker_resolver import TickerResolverService

GENERAL_SYSTEM = (
    "你是一位专业的金融助手，使用中文纯文本回答，禁止使用任何 Markdown 符号（不要 **、##、- 列表等）。"
    "直接进入正题，不要以「您好」开头，语气专业友好，回答完整清晰，不要截断。"
)


class ChatOrchestrator:
    def __init__(self) -> None:
        self.llm_service = LLMService()
        self.session_service = SessionService()
        self.router_service = RouterService(self.llm_service)
        self.ticker_resolver = TickerResolverService(self.llm_service)
        self.asset_service = AssetService(self.llm_service, self.ticker_resolver)
        fin_report = FinancialReportService(self.ticker_resolver, llm_service=self.llm_service)
        self.rag_service = RAGService(self.llm_service, fin_report_service=fin_report)

    def chat(self, session_id: str, message: str) -> tuple[int, str, dict | None]:
        self.session_service.append_message(session_id, "user", message)
        history = self.session_service.get_history(session_id)

        intent = self.router_service.classify(message)

        if intent == 1:
            response, data = self.asset_service.answer(message, history=history)
        elif intent == 2:
            response, data = self.rag_service.answer(message, history=history)
        else:
            response = self._general_answer(message, history=history)
            data = None

        self.session_service.append_message(session_id, "assistant", response)
        return intent, response, data

    def new_session(self, session_id: str) -> None:
        self.session_service.reset(session_id)

    def _general_answer(self, message: str, history: list[dict]) -> str:
        messages = [{"role": "system", "content": GENERAL_SYSTEM}]
        messages.extend(history)  # includes current user turn already appended
        if self.llm_service.available:
            return self.llm_service.chat(messages, temperature=0.3, max_tokens=800)
        return "General chat requires a configured LLM. Please set GEMINI_API_KEY in backend/.env."
