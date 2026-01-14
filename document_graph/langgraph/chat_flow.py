from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from document_graph.config import load_app_config
from document_graph.db.models import Chunk, Conversation, MemorySummary, Message, Workspace
from document_graph.openai_compat import OpenAICompatClient
from document_graph.redis_utils import cache_append_message, cache_get_recent_messages, cache_get_summary, cache_set_summary
from document_graph.token_counter import approx_message_tokens, approx_tokens


SYSTEM_PROMPT = """你是一个企业文档问答助手。
要求：
1) 只根据给定的“证据片段”回答；证据不足时，明确说“不确定/文档中未找到”并建议下一步查什么。
2) 只输出答案正文，不要在文本中附加“引用/References/证据列表”等内容；引用信息由系统单独展示。
3) 不要编造来源、不要输出与证据无关的内容。
"""


def _strip_inline_citations(answer: str) -> str:
    # UI already renders refs from structured output; keep answer concise.
    markers = ["\n引用", "\nReferences", "\n参考", "\n证据"]
    cut = None
    for m in markers:
        i = answer.find(m)
        if i != -1:
            cut = i if cut is None else min(cut, i)
    if cut is None:
        return answer.strip()
    return answer[:cut].strip()


class ChatState(TypedDict, total=False):
    workspace_id: str
    conversation_id: str
    user_message: str
    top_k: int
    qdrant_collection: str
    history: list[dict[str, str]]
    memory_summary: str
    retrieved_chunk_uids: list[str]
    retrieved_chunks: list[dict[str, Any]]
    answer: str
    refs: list[dict[str, Any]]


@dataclass(frozen=True)
class ChatDeps:
    db: Session
    redis: Any | None = None


logger = logging.getLogger(__name__)


def _ensure_conversation(state: ChatState, deps: ChatDeps) -> ChatState:
    workspace_id = uuid.UUID(state["workspace_id"])
    conv_id = state.get("conversation_id")
    if conv_id:
        existing = deps.db.query(Conversation).filter(Conversation.id == uuid.UUID(conv_id)).one_or_none()
        if existing is None or existing.workspace_id != workspace_id:
            raise RuntimeError("conversation_not_found")
        return {"conversation_id": conv_id}

    conv = Conversation(workspace_id=workspace_id, title="")
    deps.db.add(conv)
    deps.db.commit()
    deps.db.refresh(conv)
    return {"conversation_id": str(conv.id)}


def _load_session(state: ChatState, deps: ChatDeps) -> ChatState:
    ws = deps.db.query(Workspace).filter(Workspace.id == uuid.UUID(state["workspace_id"])).one_or_none()
    if ws is None:
        raise RuntimeError("workspace_not_found")
    return {"qdrant_collection": ws.qdrant_collection}


def _persist_user_message(state: ChatState, deps: ChatDeps) -> ChatState:
    msg = Message(
        conversation_id=uuid.UUID(state["conversation_id"]),
        role="user",
        content=state["user_message"],
        metadata_={},
    )
    deps.db.add(msg)
    deps.db.commit()
    if deps.redis is not None:
        try:
            cache_append_message(
                deps.redis,
                conversation_id=state["conversation_id"],
                role="user",
                content=state["user_message"],
                metadata={},
                max_messages=50,
                ttl_s=7 * 24 * 3600,
            )
        except Exception:
            pass
    return {}


def _load_memory(state: ChatState, deps: ChatDeps) -> ChatState:
    conv_id = uuid.UUID(state["conversation_id"])
    memory_summary = ""
    history: list[dict[str, str]] = []

    if deps.redis is not None:
        cached_summary = cache_get_summary(deps.redis, conversation_id=str(conv_id))
        if cached_summary is not None:
            memory_summary = cached_summary
        cached_messages = cache_get_recent_messages(deps.redis, conversation_id=str(conv_id), limit=50)
        if cached_messages is not None:
            history = [
                {"role": str(m.get("role") or ""), "content": str(m.get("content") or "")}
                for m in cached_messages
                if m.get("role") and m.get("content") is not None
            ]

    if memory_summary == "":
        summary = deps.db.query(MemorySummary).filter(MemorySummary.conversation_id == conv_id).one_or_none()
        memory_summary = summary.summary if summary else ""
        if deps.redis is not None:
            try:
                cache_set_summary(deps.redis, conversation_id=str(conv_id), summary=memory_summary, ttl_s=7 * 24 * 3600)
            except Exception:
                pass

    if not history:
        rows = (
            deps.db.query(Message)
            .filter(Message.conversation_id == conv_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(50)
            .all()
        )
        rows.reverse()
        history = [{"role": r.role, "content": r.content} for r in rows]
        if deps.redis is not None and rows:
            try:
                for r in rows:
                    cache_append_message(
                        deps.redis,
                        conversation_id=str(conv_id),
                        role=r.role,
                        content=r.content,
                        metadata=getattr(r, "metadata_", {}) or {},
                        max_messages=50,
                        ttl_s=7 * 24 * 3600,
                    )
            except Exception:
                pass

    return {"history": history, "memory_summary": memory_summary}


def _retrieve_vectors(state: ChatState, deps: ChatDeps) -> ChatState:
    cfg = load_app_config()
    embed = OpenAICompatClient(base_url=cfg.embedding.base_url, api_key=cfg.embedding.api_key)
    qdrant = QdrantClient(url=cfg.qdrant.url)

    q_vec = embed.embeddings(model=cfg.embedding.model, inputs=[state["user_message"]])[0]

    query_vec: Any = q_vec
    if cfg.multimodal.enabled:
        try:
            info = qdrant.get_collection(state["qdrant_collection"])
            vectors = info.config.params.vectors
            if isinstance(vectors, dict) and "text" in vectors:
                query_vec = ("text", q_vec)
        except Exception:
            # Fall back to single-vector query.
            query_vec = q_vec
    try:
        limit = int(state.get("top_k") or 8)
        if hasattr(qdrant, "search"):
            hits = qdrant.search(
                collection_name=state["qdrant_collection"],
                query_vector=query_vec,
                limit=limit,
                with_payload=True,
            )
        else:
            result = qdrant.query_points(
                collection_name=state["qdrant_collection"],
                query=query_vec,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            hits = list(getattr(result, "points", []) or [])
    except Exception as e:
        logger.warning("qdrant_search_failed error=%s", e)
        return {"retrieved_chunk_uids": []}
    chunk_uids: list[str] = []
    for h in hits:
        payload = h.payload or {}
        cu = payload.get("chunk_uid")
        if isinstance(cu, str) and cu:
            chunk_uids.append(cu)
    return {"retrieved_chunk_uids": chunk_uids}


def _hydrate_chunks(state: ChatState, deps: ChatDeps) -> ChatState:
    uids = state.get("retrieved_chunk_uids") or []
    if not uids:
        return {"retrieved_chunks": []}

    rows = deps.db.query(Chunk).filter(Chunk.chunk_uid.in_(uids)).all()
    by_uid = {r.chunk_uid: r for r in rows}
    out: list[dict[str, Any]] = []
    for uid in uids:
        r = by_uid.get(uid)
        if r is None:
            continue
        out.append(
            {
                "chunk_uid": r.chunk_uid,
                "title_path": r.title_path,
                "offset_start": r.offset_start,
                "offset_end": r.offset_end,
                "text": r.text,
            }
        )
    if not out:
        logger.info("no_chunks_hydrated uids=%s", len(uids))
    return {"retrieved_chunks": out}


def _trim_history_by_tokens(state: ChatState, *, max_context_tokens: int, reserved_for_output: int) -> list[dict[str, str]]:
    history = state.get("history") or []
    summary = state.get("memory_summary") or ""

    fixed = approx_tokens(SYSTEM_PROMPT) + approx_tokens(summary) + approx_message_tokens("user", state["user_message"])
    evidence = 0
    for c in state.get("retrieved_chunks") or []:
        evidence += approx_tokens(str(c.get("text") or "")) + 16

    budget = max_context_tokens - reserved_for_output
    if fixed + evidence >= budget:
        return []

    remaining = budget - fixed - evidence
    trimmed: list[dict[str, str]] = []
    for m in reversed(history):
        t = approx_message_tokens(m.get("role", ""), m.get("content", ""))
        if t > remaining:
            break
        trimmed.append(m)
        remaining -= t
    trimmed.reverse()
    return trimmed


def _answer_with_citations(state: ChatState, deps: ChatDeps) -> ChatState:
    cfg = load_app_config()
    llm = OpenAICompatClient(base_url=cfg.llm.base_url, api_key=cfg.llm.api_key)

    chunks = state.get("retrieved_chunks") or []
    evidence_blocks = []
    refs: list[dict[str, Any]] = []
    for i, c in enumerate(chunks, start=1):
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{i}] chunk_uid: {c.get('chunk_uid','')}",
                    f"title_path: {c.get('title_path', [])}",
                    f"offset: {c.get('offset_start')}..{c.get('offset_end')}",
                    f"text: {c.get('text','')}",
                ]
            )
        )
        refs.append(
            {
                "i": i,
                "chunk_uid": c.get("chunk_uid"),
                "title_path": c.get("title_path"),
                "offset_start": c.get("offset_start"),
                "offset_end": c.get("offset_end"),
            }
        )

    trimmed_history = _trim_history_by_tokens(state, max_context_tokens=8192, reserved_for_output=1024)
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if state.get("memory_summary"):
        messages.append({"role": "system", "content": f"对话摘要（供参考）：\n{state['memory_summary']}"})
    messages.extend(trimmed_history)

    user_prompt = "\n\n".join(
        [
            f"问题：{state['user_message']}",
            "证据片段如下（只允许使用这些内容作答）：",
            "\n\n".join(evidence_blocks) if evidence_blocks else "(无证据命中)",
        ]
    )
    messages.append({"role": "user", "content": user_prompt})

    answer = llm.chat_completions(model=cfg.llm.model, messages=messages, temperature=cfg.llm.temperature)
    return {"answer": _strip_inline_citations(answer), "refs": refs}


def _persist_assistant_message(state: ChatState, deps: ChatDeps) -> ChatState:
    msg = Message(
        conversation_id=uuid.UUID(state["conversation_id"]),
        role="assistant",
        content=state["answer"],
        metadata_={"refs": state.get("refs") or []},
    )
    deps.db.add(msg)
    deps.db.commit()
    if deps.redis is not None:
        try:
            cache_append_message(
                deps.redis,
                conversation_id=state["conversation_id"],
                role="assistant",
                content=state["answer"],
                metadata={"refs": state.get("refs") or []},
                max_messages=50,
                ttl_s=7 * 24 * 3600,
            )
        except Exception:
            pass
    return {}


def build_chat_graph(deps: ChatDeps) -> Any:
    g = StateGraph(ChatState)
    g.add_node("ensure_conversation", lambda s: _ensure_conversation(s, deps))
    g.add_node("load_session", lambda s: _load_session(s, deps))
    g.add_node("persist_user_message", lambda s: _persist_user_message(s, deps))
    g.add_node("load_memory", lambda s: _load_memory(s, deps))
    g.add_node("retrieve_vectors", lambda s: _retrieve_vectors(s, deps))
    g.add_node("hydrate_chunks", lambda s: _hydrate_chunks(s, deps))
    g.add_node("answer_with_citations", lambda s: _answer_with_citations(s, deps))
    g.add_node("persist_assistant_message", lambda s: _persist_assistant_message(s, deps))

    g.set_entry_point("ensure_conversation")
    g.add_edge("ensure_conversation", "load_session")
    g.add_edge("load_session", "persist_user_message")
    g.add_edge("persist_user_message", "load_memory")
    g.add_edge("load_memory", "retrieve_vectors")
    g.add_edge("retrieve_vectors", "hydrate_chunks")
    g.add_edge("hydrate_chunks", "answer_with_citations")
    g.add_edge("answer_with_citations", "persist_assistant_message")
    g.add_edge("persist_assistant_message", END)
    return g.compile()

def run_chat(
    *,
    db: Session,
    workspace_id: str,
    conversation_id: str | None,
    user_message: str,
    top_k: int = 8,
    redis: Any | None = None,
) -> ChatState:
    deps = ChatDeps(db=db, redis=redis)
    graph = build_chat_graph(deps)
    state: ChatState = {
        "workspace_id": workspace_id,
        "conversation_id": conversation_id or "",
        "user_message": user_message,
        "top_k": int(top_k),
    }
    if not conversation_id:
        state.pop("conversation_id", None)
    return graph.invoke(state)
