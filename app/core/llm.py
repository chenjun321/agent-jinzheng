from openai import OpenAI

from app.core.config import Settings


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.dashscope_api_key:
            self.client = OpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.effective_dashscope_base_url,
            )

    def answer(self, question: str, evidence: list[dict]) -> str:
        if not self.client:
            return self._fallback_answer(question, evidence)

        max_input_chars = int(self.settings.cfg("models.max_input_tokens", 6000)) * 3
        evidence_text = "\n\n".join(
            f"[证据{i + 1} | 页码 {item['page_start']}-{item['page_end']}]\n{item['snippet']}"
            for i, item in enumerate(evidence)
        )[:max_input_chars]
        few_shot = ""
        if bool(self.settings.cfg("agent.few_shot.enabled", True)):
            examples = self.settings.cfg("agent.few_shot.examples", [])
            few_shot = "\n".join(
                f"- 用户问：{item.get('question')}\n  回答风格：{item.get('answer_style')}"
                for item in examples
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严谨的文档问答 Agent。只能依据给定证据回答。"
                    "如果证据不足，必须说明文档中没有找到足够依据。"
                    "回答要简洁，并保留关键条款或表格信息。"
                    f"\nFew-shot 风格参考：\n{few_shot}"
                ),
            },
            {
                "role": "user",
                "content": f"问题：{question}\n\n证据：\n{evidence_text}",
            },
        ]
        try:
            resp = self.client.chat.completions.create(
                model=self.settings.effective_chat_model,
                messages=messages,
                temperature=float(self.settings.cfg("models.temperature", 0.1)),
                max_tokens=int(self.settings.cfg("models.max_output_tokens", 900)),
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return self._fallback_answer(question, evidence)

    def _fallback_answer(self, question: str, evidence: list[dict]) -> str:
        if not evidence:
            return "当前文档中没有找到足够依据回答该问题。"
        lines = []
        for item in evidence[:3]:
            snippet = item["snippet"].strip()
            if len(snippet) > 260:
                snippet = snippet[:260] + "..."
            lines.append(f"第 {item['page_start']}-{item['page_end']} 页：{snippet}")
        return "根据检索到的文档证据，可以参考以下内容：\n" + "\n".join(lines)
