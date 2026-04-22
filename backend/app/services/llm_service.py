import logging
import textwrap

from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)

# 单独的 LLM trace logger，写入 logs/llm.log
llm_trace = logging.getLogger("llm.trace")


class LLMService:
    """Gemini LLM wrapper using the google-genai SDK."""

    def __init__(self) -> None:
        self.available = bool(settings.gemini_api_key)
        if self.available:
            self._client = genai.Client(api_key=settings.gemini_api_key)
        else:
            logger.warning("GEMINI_API_KEY not set — LLM features disabled.")
            self._client = None
        self._call_count = 0

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int = 800,
        thinking: bool = True,
    ) -> str:
        """
        messages : list of {"role": "user"|"assistant"|"system", "content": str}
        thinking  : False → 禁用 Gemini 内部推理（适合短提取任务，避免 token 被思考消耗）
        """
        if not self.available or self._client is None:
            return "[LLM unavailable: GEMINI_API_KEY not configured]"

        temp = temperature if temperature is not None else settings.llm_temperature
        self._call_count += 1
        call_id = self._call_count

        # 分离 system / turns
        system_parts: list[str] = []
        turns: list[dict] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                turns.append(msg)

        system_instruction = "\n\n".join(system_parts) if system_parts else None

        # 构建历史（不含最后一条）
        history: list[types.Content] = []
        for turn in turns[:-1]:
            role = "model" if turn["role"] == "assistant" else "user"
            history.append(
                types.Content(role=role, parts=[types.Part(text=turn["content"])])
            )

        last_content = turns[-1]["content"] if turns else (system_instruction or "")

        # thinking_budget=0 → 完全关闭思考，直接输出（短任务用）
        thinking_cfg = (
            types.ThinkingConfig(thinking_budget=0)
            if not thinking
            else None
        )

        config = types.GenerateContentConfig(
            temperature=temp,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
            thinking_config=thinking_cfg,
        )

        self._log_prompt(call_id, system_instruction, history, last_content,
                         temp, max_tokens, thinking)

        try:
            chat_session = self._client.chats.create(
                model=settings.gemini_model,
                history=history,
                config=config,
            )
            response = chat_session.send_message(last_content)
            result = (response.text or "").strip()
            self._log_response(call_id, result)
            return result
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            llm_trace.error("[Call #%d] ERROR: %s", call_id, exc)
            return f"[LLM error: {exc}]"

    # ── logging helpers ───────────────────────────────────────────────────────

    def _log_prompt(
        self,
        call_id: int,
        system: str | None,
        history: list,
        last_msg: str,
        temperature: float,
        max_tokens: int,
        thinking: bool,
    ) -> None:
        sep = "─" * 70
        lines = [
            f"\n{'═'*70}",
            f"  LLM Call #{call_id}  |  model={settings.gemini_model}"
            f"  temp={temperature}  max_tokens={max_tokens}  thinking={thinking}",
            f"{'═'*70}",
        ]
        if system:
            lines += [f"[SYSTEM]\n{textwrap.indent(system, '  ')}", sep]
        for i, h in enumerate(history):
            role = h.role.upper()
            text = h.parts[0].text if h.parts else ""
            lines += [f"[HISTORY {i+1} | {role}]\n{textwrap.indent(text, '  ')}", sep]
        lines += [f"[USER]\n{textwrap.indent(last_msg, '  ')}"]
        llm_trace.debug("\n".join(lines))

    def _log_response(self, call_id: int, response: str) -> None:
        lines = [
            "─" * 70,
            f"[RESPONSE to Call #{call_id}]",
            textwrap.indent(response, "  "),
            "═" * 70,
        ]
        llm_trace.debug("\n".join(lines))
