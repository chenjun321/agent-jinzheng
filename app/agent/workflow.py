import uuid
from datetime import datetime, timedelta, timezone
import re

from app.core.llm import LLMClient
from app.rag.service import RAGService
from app.storage.sqlite_store import SQLiteStore


class DocumentQAAgent:
    def __init__(self, store: SQLiteStore, rag: RAGService, llm: LLMClient):
        self.store = store
        self.rag = rag
        self.llm = llm

    def ask(self, doc_id: str, question: str, top_k: int = 5, session_id: str | None = None) -> dict:
        session_id = self._touch_session(doc_id, session_id)
        questions = self._split_questions(question)
        if len(questions) > 1:
            answers = [self._answer_single(doc_id, item, top_k) for item in questions]
            result = {
                "question": question,
                "answer": "\n\n".join(f"{idx + 1}. {item['question']}\n{item['answer']}" for idx, item in enumerate(answers)),
                "sub_answers": answers,
                "citations": [citation for item in answers for citation in item["citations"]],
                "evidence": [ev for item in answers for ev in item["evidence"]],
                "self_check": self._combine_self_checks([item["self_check"] for item in answers]),
            }
            qa_log_id = self._log_result(doc_id, result, session_id)
            result["qa_log_id"] = qa_log_id
            result["session_id"] = session_id
            self._log_chat_messages(doc_id, session_id, question, result)
            return result
        result = self._answer_single(doc_id, question, top_k)
        qa_log_id = self._log_result(doc_id, result, session_id)
        result["qa_log_id"] = qa_log_id
        result["session_id"] = session_id
        self._log_chat_messages(doc_id, session_id, question, result)
        return result

    def _answer_single(self, doc_id: str, question: str, top_k: int = 5) -> dict:
        evidence = self.rag.retrieve(doc_id, question, top_k=top_k)
        self_check = self._precheck(question, evidence)
        if self_check["action"] == "refuse":
            answer = self.rag.settings.cfg("agent.refusal_message", "当前文档中没有找到足够依据回答该问题。")
            citations = []
        else:
            evidence_for_answer = int(self.rag.settings.cfg("agent.evidence_for_answer", 3))
            answer = self.llm.answer(question, evidence[:evidence_for_answer])
            citations = [
                {
                    "page_start": item["page_start"],
                    "page_end": item["page_end"],
                    "chunk_id": item["chunk_id"],
                    "snippet": item["snippet"],
                    "score": item["score"],
                    "confidence": item.get("confidence"),
                    "rerank_score": item.get("rerank_score"),
                }
                for item in evidence[:evidence_for_answer]
            ]
            self_check = self._postcheck(answer, evidence)

        return {
            "question": question,
            "answer": answer,
            "citations": citations,
            "evidence": evidence,
            "self_check": self_check,
            "unanswered": self._build_unanswered_payload(evidence, self_check),
        }

    def _log_result(self, doc_id: str, result: dict, session_id: str) -> str:
        qa_log_id = uuid.uuid4().hex
        self.store.insert_qa_log(
            {
                "id": qa_log_id,
                "doc_id": doc_id,
                "session_id": session_id,
                "question": result["question"],
                "answer": result["answer"],
                "citations": result["citations"],
                "self_check": result["self_check"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return qa_log_id

    def _precheck(self, question: str, evidence: list[dict]) -> dict:
        if not evidence:
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "没有检索到相关证据"}
        best = evidence[0]["score"]
        confidence = evidence[0].get("confidence", best)
        min_score = float(self.rag.settings.cfg("agent.answer_min_score", 0.08))
        min_confidence = float(self.rag.settings.cfg("agent.answer_min_confidence", 0.15))
        if best < min_score:
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "检索相关性分数过低"}
        if bool(self.rag.settings.cfg("agent.refuse_below_confidence", True)) and confidence < min_confidence:
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "证据置信度低于输出阈值"}
        if not self._evidence_covers_question(question, evidence):
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "检索结果没有覆盖问题关键词"}
        out_of_scope_keywords = self.rag.settings.cfg("agent.out_of_scope_keywords", ["发动机", "股票", "天气", "旅游", "做饭"])
        if any(word in question for word in out_of_scope_keywords):
            if best < float(self.rag.settings.cfg("agent.out_of_scope_min_score", 0.45)):
                return {"grounded": False, "risk": "high", "action": "refuse", "reason": "问题疑似超出本文档范围"}
        return {"grounded": True, "risk": "low", "action": "answer", "reason": "检索到可引用证据"}

    def _postcheck(self, answer: str, evidence: list[dict]) -> dict:
        if not evidence:
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "答案缺少证据"}
        refusal_signals = [
            "没有找到足够依据",
            "没有找到",
            "未找到",
            "无法确认",
            "不能确认",
            "无法回答",
            "没有相关信息",
            "没有任何信息",
            "证据不足",
        ]
        if any(signal in answer for signal in refusal_signals):
            return {"grounded": False, "risk": "medium", "action": "refuse", "reason": "生成阶段判断证据不足"}
        return {"grounded": True, "risk": "low", "action": "answer", "reason": "答案已绑定检索证据和页码引用"}

    def _evidence_covers_question(self, question: str, evidence: list[dict]) -> bool:
        keywords = self._question_keywords(question)
        if not keywords:
            return True
        text = "".join((item.get("snippet") or "") + " " + (item.get("title") or "") for item in evidence[:3])
        text = re.sub(r"\s+", "", text)
        matched = [keyword for keyword in keywords if keyword in text]
        return bool(matched)

    def _question_keywords(self, question: str) -> list[str]:
        stopwords = {
            "当",
            "时",
            "这个",
            "这份",
            "标准",
            "文档",
            "的",
            "是",
            "是否",
            "规定",
            "要求",
            "包括",
            "包含",
            "内容",
            "什么时候",
            "什么时间",
            "什么",
            "时候",
            "何时",
            "哪些",
            "多少",
            "多大",
            "如何",
            "怎么",
            "有没有",
            "关于",
            "里面",
            "需要",
        }
        domain_terms = [
            "规范性引用文件",
            "引用文件",
            "抗拉强度",
            "裂纹",
            "氧化皮",
            "毛刺",
            "半圆键",
            "圆角",
            "平行度",
            "楔键",
            "斜度",
            "角度公差",
            "合格判定数",
            "抗拉强度试验",
            "抽样方案",
            "尺寸检查",
            "检查项目",
            "合格质量水平",
            "包装箱",
            "包装",
            "标志",
            "标签",
            "制造厂名",
            "产品名称",
            "防锈剂",
            "防锈",
            "发布日期",
            "实施日期",
            "发布",
            "实施",
            "范围",
            "验收检查",
            "表1",
            "AQL",
            "Ac",
            "GB/T",
        ]
        normalized_question = re.sub(r"\s+", "", question)
        keywords = [term for term in domain_terms if term in normalized_question]
        tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", question)
        for token in tokens:
            if token in stopwords:
                continue
            parts = [token]
            for word in stopwords:
                next_parts = []
                for part in parts:
                    next_parts.extend(item for item in part.split(word) if item)
                parts = next_parts
            keywords.extend(re.sub(r"\s+", "", part) for part in parts if len(part) >= 2 and part not in stopwords)
        return list(dict.fromkeys(keywords))[:10]

    def _split_questions(self, question: str) -> list[str]:
        if not bool(self.rag.settings.cfg("agent.multi_question.enabled", True)):
            return [question]
        separators = self.rag.settings.cfg("agent.multi_question.separators", ["？", "?", "；", ";", "\n"])
        parts = [question.strip()]
        for sep in separators:
            next_parts = []
            for part in parts:
                next_parts.extend(item.strip() for item in part.split(sep) if item.strip())
            parts = next_parts
        if len(parts) <= 1:
            return [question]
        return self._carry_context_to_short_questions(parts)

    def _carry_context_to_short_questions(self, parts: list[str]) -> list[str]:
        context = self._leading_question_context(parts[0])
        if not context:
            return parts
        contextualized = [parts[0]]
        for part in parts[1:]:
            if self._needs_previous_context(part) and context not in part:
                contextualized.append(f"{context}{part}")
            else:
                contextualized.append(part)
        return contextualized

    def _leading_question_context(self, text: str) -> str:
        markers = [
            "什么时候",
            "什么时间",
            "何时",
            "有哪些",
            "包括",
            "是什么",
            "多少",
            "如何",
            "怎么",
            "是否",
            "有没有",
            "什么",
            "哪些",
        ]
        positions = [text.find(marker) for marker in markers if marker in text]
        if not positions:
            return ""
        context = text[: min(positions)].strip(" ，,。；;：:")
        return context if len(context) >= 2 else ""

    def _needs_previous_context(self, text: str) -> bool:
        return text.startswith(("什么时候", "什么时间", "何时"))

    def _combine_self_checks(self, checks: list[dict]) -> dict:
        if all(check["action"] == "answer" for check in checks):
            return {"grounded": True, "risk": "low", "action": "answer", "reason": "多个子问题均找到可引用证据"}
        if all(check["action"] == "refuse" for check in checks):
            return {"grounded": False, "risk": "high", "action": "refuse", "reason": "多个子问题均未找到足够依据"}
        return {"grounded": True, "risk": "medium", "action": "partial", "reason": "部分子问题找到依据，部分子问题已拒答"}

    def _touch_session(self, doc_id: str, session_id: str | None) -> str:
        now = datetime.now(timezone.utc)
        session_id = session_id or uuid.uuid4().hex
        existing = self.store.get_session(session_id)
        timeout = timedelta(minutes=int(self.rag.settings.cfg("session.idle_timeout_minutes", 30)))
        if existing:
            last_active = datetime.fromisoformat(existing["last_active_at"])
            if existing["status"] != "active" or now - last_active > timeout:
                self.store.archive_session(session_id, now.isoformat())
                session_id = uuid.uuid4().hex
                existing = None
        self.store.upsert_session(
            {
                "id": session_id,
                "doc_id": doc_id,
                "status": "active",
                "started_at": existing["started_at"] if existing else now.isoformat(),
                "last_active_at": now.isoformat(),
                "archived_at": None,
            }
        )
        return session_id

    def _log_chat_messages(self, doc_id: str, session_id: str, question: str, result: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.insert_chat_message(
            {
                "id": uuid.uuid4().hex,
                "session_id": session_id,
                "doc_id": doc_id,
                "role": "user",
                "content": question,
                "payload": {},
                "created_at": now,
            }
        )
        self.store.insert_chat_message(
            {
                "id": uuid.uuid4().hex,
                "session_id": session_id,
                "doc_id": doc_id,
                "role": "assistant",
                "content": result["answer"],
                "payload": result,
                "created_at": now,
            }
        )

    def _build_unanswered_payload(self, evidence: list[dict], self_check: dict) -> dict:
        if self_check["action"] != "refuse":
            return {"handled": False}
        return {
            "handled": True,
            "message": self.rag.settings.cfg("agent.refusal_message", "当前文档中没有找到足够依据回答该问题。"),
            "nearest_evidence": evidence[:3],
            "suggestions": [
                "换一种更贴近文档条款的问法",
                "检查是否上传了正确文档",
                "需要人工复核或补充资料",
            ],
        }
