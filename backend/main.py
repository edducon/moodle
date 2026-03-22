import re
import json
import copy
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from config import settings
from database import SessionLocal, Course, ModuleIndex, ChatLog, CourseParticipant
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Moodle Assistant API")

client = OpenAI(base_url=settings.OLLAMA_URL, api_key="ollama")
embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# BASE TEXT HELPERS
# =========================

def normalize_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def safe_strip(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_json_from_text(text: str) -> dict:
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            raw = text[start:end + 1]
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raw = re.sub(r",\s*}", "}", raw)
                raw = re.sub(r",\s*\]", "]", raw)
                return json.loads(raw.replace("\n", " ").replace("\r", ""))
    except:
        pass
    return {}


# =========================
# SEMANTIC ROUTER
# =========================

class SemanticRouter:
    def __init__(self, embedder):
        self.embedder = embedder

        self.intent_examples = {
            "teacher_info": [
                "кто преподаватель", "кто ведет курс", "контакты преподавателя",
                "как связаться с преподавателем", "кто лектор", "чья это дисциплина"
            ],
            "navigate": [
                "с чего начать", "что делать первым", "куда идти",
                "первое задание", "как начать курс", "какая первая тема"
            ],
            "grading": [
                "как получить оценку", "система оценивания", "критерии оценки",
                "как сдать экзамен", "как получить зачет", "баллы за задания",
                "как получить 5", "как получить автомат", "что будет на экзамене",
                "как получить пятерку", "как получить пятерочку", "получить хорошую оценку",
                "что нужно для отличника", "минимум для зачета",
                "система оценки дисциплины", "итоговая оценка за курс",
                "условия получения отличной оценки", "требования для получения пятерки"
            ],
            "find_deadline": [
                "когда сдавать", "дедлайн", "срок сдачи", "до какого числа",
                "горят сроки", "какие ближайшие дедлайны"
            ],
            "course_overview": [
                "из чего состоит курс", "что в курсе", "структура курса",
                "сколько заданий", "сколько лекций", "о чем этот предмет"
            ]
        }

        self.vectors = []
        self.labels = []
        self.phrases = []

        for intent, phrases in self.intent_examples.items():
            vecs = self.embedder.encode(phrases).tolist()
            for phrase, vec in zip(phrases, vecs):
                self.vectors.append(vec)
                self.labels.append(intent)
                self.phrases.append(phrase)

    def classify(self, query: str, threshold: float = 0.55) -> Tuple[str, str]:
        q_vec = self.embedder.encode([query])[0]

        similarities = cosine_similarity([q_vec], self.vectors)[0]
        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]

        if best_score >= threshold:
            best_intent = self.labels[best_idx]

            # Query Expansion
            intent_scores = [
                (similarities[i], self.phrases[i])
                for i in range(len(self.labels)) if self.labels[i] == best_intent
            ]
            intent_scores.sort(key=lambda x: x[0], reverse=True)

            expansion = " ".join([phrase for _, phrase in intent_scores[:3]])
            expanded_query = f"{query} {expansion}"

            return best_intent, expanded_query

        return "answer_from_context", query


semantic_router = SemanticRouter(embedder)


# =========================
# SCHEMAS
# =========================

class CourseData(BaseModel):
    course_id: str
    title: str
    sections: List[Dict[str, Any]]
    viewer_role: Optional[str] = None
    participants: Optional[List[Dict[str, Any]]] = []


class ModuleUpdateData(BaseModel):
    course_id: str
    moodle_id: str
    module_type: Optional[str] = None
    content_text: str
    url: str
    visibility: Optional[Dict[str, Any]] = None


class BulkModuleItem(BaseModel):
    moodle_id: str
    title: str
    module_type: Optional[str] = None
    content_text: str
    url: str
    visibility: Optional[Dict[str, Any]] = None


class BulkModuleUpdateData(BaseModel):
    course_id: str
    modules: List[BulkModuleItem]


class ChatHistoryItem(BaseModel):
    role: str
    content: str


class DeadlineItem(BaseModel):
    title: str
    due_date: str
    url: str


class SmartSearchRequest(BaseModel):
    course_id: str
    message: str
    history: List[ChatHistoryItem] = []
    viewer_role: Optional[str] = None
    deadlines: List[DeadlineItem] = []
    course_title: str = ""
    course_map: str = ""
    teachers: str = ""
    page_context: str = ""
    grades: str = ""
    assign_status: str = ""


class FeedbackRequest(BaseModel):
    log_id: int
    is_helpful: int


# =========================
# MOODLE & DOMAIN HELPERS
# =========================

ANAPHORA_MARKERS = [
    "а дальше", "что потом", "после этого", "а после",
    "ее", "её", "его", "их", "это", "эту", "этот", "этом", "там",
    "а как", "как это", "а она", "а оно", "он", "она", "они", "туда"
]

SELF_CONTAINED_MARKERS = [
    "что такое", "как работает", "почему", "зачем", "когда", "где находится",
    "нужно ли", "обязательно ли", "можно ли", "стоит ли"
]

ERROR_MARKERS = [
    "техническая ошибка", "пожалуйста, попробуйте", "произошла ошибка",
    "не проиндексирован", "не найден", "попробуйте спросить"
]


def enrich_query_with_history(user_msg: str, history: List[ChatHistoryItem]) -> str:
    msg = safe_strip(user_msg)
    msg_lower = msg.lower()

    padded_msg = f" {msg_lower} "
    has_anaphora = any(f" {marker} " in padded_msg for marker in ANAPHORA_MARKERS)
    is_self_contained = any(marker in msg_lower for marker in SELF_CONTAINED_MARKERS)

    if not has_anaphora or is_self_contained:
        return msg

    last_bot_msg = next(
        (h.content for h in reversed(history)
         if h.role == "assistant"
         and not any(err in h.content.lower() for err in ERROR_MARKERS)
         and len(h.content) > 20),
        ""
    )

    if last_bot_msg:
        clean_bot_msg = re.sub(r'<[^>]+>', '', last_bot_msg)
        return f"{clean_bot_msg[:80]} {msg}"
    return msg


def db_module_visible_for_role(mod: ModuleIndex, viewer_role: Optional[str]) -> bool:
    visibility = mod.visibility or {}
    if viewer_role == "teacher": return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False): return False
    return True


def course_module_visible_for_role(module_data: Dict[str, Any], viewer_role: Optional[str]) -> bool:
    visibility = module_data.get("visibility") or {}
    if viewer_role == "teacher": return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False): return False
    return True


def split_text_into_chunks(text: str, chunk_size: int = 450, overlap: int = 150) -> List[str]:
    if not text: return []
    text = re.sub(r"Печатать книгу.*?Оглавление", "", text, flags=re.IGNORECASE | re.DOTALL)

    paragraphs = re.split(r"\n+", text.strip())
    chunks = []
    current_chunk = ""

    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 10:
            continue

        if len(current_chunk) + len(p) + 1 <= chunk_size:
            current_chunk += ("\n" + p if current_chunk else p)
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
                overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                space_idx = overlap_text.find(' ')
                if space_idx != -1:
                    overlap_text = overlap_text[space_idx + 1:]
                current_chunk = overlap_text + "\n"
            else:
                current_chunk = ""

            while len(p) > chunk_size:
                cut_idx = p.rfind(' ', 0, chunk_size)
                if cut_idx == -1:
                    cut_idx = chunk_size

                part = current_chunk + p[:cut_idx]
                chunks.append(part.strip())
                current_chunk = ""

                overlap_start = max(0, cut_idx - overlap)
                space_overlap = p.find(' ', overlap_start)

                if space_overlap != -1 and space_overlap < cut_idx:
                    p = p[space_overlap + 1:]
                else:
                    p = p[overlap_start:]

            if len(p.strip()) >= 10:
                current_chunk += p
            else:
                current_chunk = ""

    if len(current_chunk.strip()) >= 10:
        chunks.append(current_chunk.strip())

    return chunks


def module_kind(title: str, module_type: Optional[str]) -> str:
    t, mt = normalize_text(title), normalize_text(module_type or "")
    if mt == "quiz": return "quiz"
    if mt in ("assign", "workshop"): return "assignment"
    if mt == "forum": return "forum"
    if mt in ("page", "book", "file", "folder", "url", "lesson", "resource"): return "learning"
    if "экзамен" in t or "тест" in t: return "quiz"
    if "лаборатор" in t or "практическ" in t or "задани" in t: return "assignment"
    if "форум" in t or "обсужд" in t: return "forum"
    if "лекци" in t or "вводн" in t or "литератур" in t: return "learning"
    return "other"


def parse_order_from_title(title: str) -> Tuple[int, int, int]:
    t = normalize_text(title)
    m = re.search(r"(\d+)\.(\d+)", t)
    if m: return (int(m.group(1)), int(m.group(2)), 0)
    m = re.search(r"лекц\w*\s*№\s*(\d+)", t)
    if m: return (999, int(m.group(1)), 1)
    m = re.search(r"лаборат\w*\s*работ\w*\s*№\s*(\d+)", t)
    if m: return (999, int(m.group(1)), 2)
    m = re.search(r"практическ\w*\s*(?:заняти\w*|работ\w*)\s*№\s*(\d+)", t)
    if m: return (999, int(m.group(1)), 2)
    m = re.search(r"тест\w*\s*№\s*(\d+)", t)
    if m: return (999, int(m.group(1)), 3)
    return (9999, 9999, 9999)


def build_course_modules(db_course: Course, viewer_role: str) -> List[Dict[str, Any]]:
    result = []
    sections = db_course.content or []
    for s_idx, sec in enumerate(sections):
        for m_idx, mod in enumerate(sec.get("modules", [])):
            if not course_module_visible_for_role(mod, viewer_role): continue
            title = mod.get("title", "Без названия")
            module_type = mod.get("type")
            result.append({
                "moodle_id": mod.get("moodle_id"), "title": title, "url": mod.get("url", ""),
                "module_type": module_type, "kind": module_kind(title, module_type),
                "section_index": s_idx, "module_index": m_idx, "order_key": parse_order_from_title(title),
                "visibility": mod.get("visibility") or {}
            })
    return result


def order_modules(course_modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(course_modules, key=lambda m: (m["order_key"], m["section_index"], m["module_index"]))


def extract_last_bot_navigation_target(history: List[ChatHistoryItem]) -> str:
    for item in reversed(history):
        if item.role != "assistant": continue
        text = item.content or ""
        m = re.search(r'["«](.+?)["»]', text)
        if m: return m.group(1).strip()
        m = re.search(r"откройте\s+(?:сначала\s+)?(.+?)(?:\.|$)", text, flags=re.IGNORECASE)
        if m: return m.group(1).strip()

        m = re.search(
            r"(лекци[ия]\s*№?\s*\d+|лаборатор\w+\s*работ\w*\s*№?\s*\d+|практическ\w+\s*\w*\s*№?\s*\d+|тем[аы]\s*№?\s*\d+)",
            text, flags=re.IGNORECASE)
        if m: return m.group(1).strip()
    return ""


def get_next_module_after(course_modules: List[Dict[str, Any]], current_title: str) -> Optional[Dict[str, Any]]:
    if not current_title: return None
    ordered = order_modules(course_modules)
    current_norm = normalize_text(current_title)
    idx = next((i for i, item in enumerate(ordered) if normalize_text(item["title"]) == current_norm), None)
    if idx is not None and idx + 1 < len(ordered): return ordered[idx + 1]
    return None


def choose_default_start(course_modules: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ordered = order_modules(course_modules)
    if not ordered: return None

    def score(m: Dict[str, Any]) -> Tuple[int, Tuple[int, int, int], int, int]:
        title = normalize_text(m["title"])
        bias = 0
        if "вводн" in title:
            bias -= 100
        elif "описание дисциплины" in title:
            bias -= 80
        elif m["kind"] == "learning":
            bias -= 30
        elif m["kind"] == "assignment":
            bias -= 10
        return (bias, m["order_key"], m["section_index"], m["module_index"])

    return sorted(ordered, key=score)[0]


def build_course_ontology(course_modules: List[Dict[str, Any]], deadlines: List[DeadlineItem], course_title: str) -> \
        Dict[str, Any]:
    ordered = order_modules(course_modules)
    return {
        "course_title": course_title,
        "counts": {
            "all": len(ordered), "learning": len([m for m in ordered if m["kind"] == "learning"]),
            "assignments": len([m for m in ordered if m["kind"] == "assignment"]),
            "quizzes": len([m for m in ordered if m["kind"] == "quiz"]),
            "forums": len([m for m in ordered if m["kind"] == "forum"]),
        },
        "start_module": choose_default_start(course_modules),
        "deadlines": deadlines,
    }


# =========================
# ROUTING & RETRIEVAL
# =========================

def route_request(enriched_msg: str, ontology: Dict[str, Any], has_deadlines: bool) -> Dict[str, Any]:
    intent, search_query = semantic_router.classify(enriched_msg)
    route = {"action": intent, "scope": "generic", "query": search_query}

    if intent == "find_deadline" and not has_deadlines:
        route["action"] = "answer_from_context"
    elif intent == "grading":
        route["action"] = "answer_from_context"

    return route


def retrieve_candidates(db: Session, course_id: str, viewer_role: str, search_query: str) -> Tuple[
    List[Dict[str, Any]], Dict[str, float]]:
    query_vector = embedder.encode([search_query])[0].tolist()
    distance_col = ModuleIndex.embedding.cosine_distance(query_vector)

    raw_results = db.query(ModuleIndex, distance_col.label("distance")).filter(
        ModuleIndex.course_id == course_id
    ).order_by(distance_col).limit(15).all()

    scored = []
    for c, dist in raw_results:
        if not db_module_visible_for_role(c, viewer_role): continue
        scored.append((dist, {
            "id": str(c.id), "moodle_id": c.moodle_id, "url": c.url,
            "title": c.title, "kind": module_kind(c.title or "", c.module_type),
            "content_text": c.content_text, "module_type": c.module_type,
        }))

    scored.sort(key=lambda x: x[0])
    candidates = [item for _, item in scored[:8]]
    score_map = {item["id"]: round(score, 4) for score, item in scored[:15]}
    return candidates, score_map


# =========================
# EXECUTORS
# =========================

def exec_navigation(course_modules: List[Dict[str, Any]], history: List[ChatHistoryItem]) -> Dict[str, Any]:
    last_target = extract_last_bot_navigation_target(history)
    if last_target:
        nxt = get_next_module_after(course_modules, last_target)
        if nxt: return {"facts": {"mode": "next_step", "current_target": last_target, "next_module": nxt},
                        "targets": [nxt]}
    start = choose_default_start(course_modules)
    return {"facts": {"mode": "start", "start_module": start}, "targets": [start] if start else []}


def exec_course_overview(ontology: Dict[str, Any]) -> Dict[str, Any]:
    return {"facts": {"course_title": ontology.get("course_title", ""), "counts": ontology.get("counts", {}),
                      "start_module": ontology.get("start_module")},
            "targets": [ontology.get("start_module")] if ontology.get("start_module") else []}


def exec_deadlines(deadlines: List[DeadlineItem], scope: str) -> Dict[str, Any]:
    return {"facts": {"has_deadlines": bool(deadlines), "deadlines": deadlines[:10], "scope": scope}, "targets": []}


def exec_answer_from_context(db: Session, course_id: str, viewer_role: str, query: str) -> Dict[str, Any]:
    candidates, score_map = retrieve_candidates(db=db, course_id=course_id, viewer_role=viewer_role, search_query=query)

    for c in candidates:
        snippet_raw = c.get("content_text", "")[:80].strip()
        c["snippet"] = re.sub(r'\s+', ' ', snippet_raw)

    return {"facts": {"query": query, "candidates": candidates[:3]}, "targets": candidates[:1], "score_map": score_map}


# =========================
# GENERATOR
# =========================

def generate_response(
        user_msg: str,
        history: List[ChatHistoryItem],
        action: str,
        execution: Dict[str, Any],
        ontology: Dict[str, Any]
) -> Dict[str, Any]:
    facts = execution.get("facts", {})
    targets = [t for t in execution.get("targets", []) if t]

    prompt = f"""
Ты интеллектуальный помощник для студентов курса в Moodle.
Отвечай вежливо, естественно и полезно.
Используй ТОЛЬКО переданные факты, ничего не придумывай.
Если студент спрашивает определение термина, найди наиболее полное определение в фактах и приведи его ПОЛНОСТЬЮ.
Если по фактам нет нужной информации, честно скажи, что не обладаешь такими данными.
Если предлагаешь куда-то перейти, мягко сошлись на это.

Верни СТРОГО JSON:
{{
  "reply": "твой ответ студенту"
}}

--- КОНТЕКСТ ---
Запрос: {user_msg}
Курс: {ontology.get("course_title", "Без названия")}

Найденные факты из базы для ответа:
{json.dumps(facts, ensure_ascii=False, default=str)}
"""
    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            response_format={"type": "json_object"},
            extra_body={"options": {"num_ctx": 8192}}
        )

        raw_content = resp.choices[0].message.content.strip()
        data = extract_json_from_text(raw_content)
        reply = safe_strip(data.get("reply", ""))

        if not reply and raw_content:
            if not raw_content.startswith("{"):
                reply = raw_content

        if reply:
            return {"reply": reply, "targets": targets}

    except Exception as e:
        print(f"Ошибка LLM-генерации: {e}")
        if 'resp' in locals() and resp.choices:
            raw = resp.choices[0].message.content.strip()
            recovered = extract_json_from_text(raw)
            if recovered and recovered.get("reply"):
                return {"reply": recovered["reply"], "targets": targets}
            if not raw.startswith("{"):
                return {"reply": raw, "targets": targets}

    return {
        "reply": "Произошла техническая ошибка при формулировании ответа. Пожалуйста, попробуйте спросить чуть иначе.",
        "targets": targets}


# =========================
# ENDPOINTS
# =========================

@app.post("/api/course/sync")
def sync_course(data: CourseData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    is_new = False
    if db_course:
        db_course.title = data.title
        db_course.content = data.sections
        db_course.last_updated = datetime.now(timezone.utc)
    else:
        db_course = Course(course_id=data.course_id, title=data.title, content=data.sections)
        db.add(db_course)
        is_new = True

    if data.participants is not None:
        db.query(CourseParticipant).filter(CourseParticipant.course_id == data.course_id).delete()
        for p in data.participants:
            db.add(CourseParticipant(
                course_id=data.course_id,
                name=p.get("name", ""),
                role=p.get("role", "Студент"),
                group_name=p.get("group_name", "")
            ))

    db.commit()
    indexed_count = db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count()
    return {"status": "success", "needs_initial_sync": is_new or (indexed_count == 0)}


@app.post("/api/module/update")
def update_module_content(data: ModuleUpdateData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course: return {"status": "error", "reason": "course_not_found"}
    sections = copy.deepcopy(db_course.content)
    mod_title, mod_type = "Без названия", data.module_type
    for sec in sections:
        for mod in sec.get("modules", []):
            if mod.get("moodle_id") == data.moodle_id:
                mod["content_text"] = "Текст обновлен"
                mod["url"] = data.url
                mod["visibility"] = data.visibility or {}
                mod_title = mod.get("title", "Без названия")
                mod_type = mod.get("type", mod_type)
                break
    db_course.content = sections
    db_course.last_updated = datetime.now(timezone.utc)
    db.query(ModuleIndex).filter(ModuleIndex.moodle_id == data.moodle_id).delete()

    chunks = split_text_into_chunks(data.content_text) or ["(Нет текста)"]
    vectors = embedder.encode([f"{mod_title}\n{chunk}" for chunk in chunks]).tolist()
    for chunk, vector in zip(chunks, vectors):
        db.add(ModuleIndex(
            moodle_id=data.moodle_id, course_id=data.course_id, module_type=mod_type,
            title=mod_title, content_text=chunk, url=data.url, visibility=data.visibility or {}, embedding=vector
        ))
    db.commit()
    return {"status": "success"}


@app.post("/api/module/bulk-update")
def bulk_update_modules(data: BulkModuleUpdateData, db: Session = Depends(get_db)):
    moodle_ids = [m.moodle_id for m in data.modules]
    if moodle_ids:
        db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id,
                                     ModuleIndex.moodle_id.in_(moodle_ids)).delete(synchronize_session=False)

    texts_to_embed, chunk_metadata = [], []
    for inc_mod in data.modules:
        chunks = split_text_into_chunks(inc_mod.content_text)
        if not chunks: continue
        for chunk in chunks:
            texts_to_embed.append(f"{inc_mod.title or 'Без названия'}\n{chunk}")
            chunk_metadata.append(
                {"moodle_id": inc_mod.moodle_id, "module_type": inc_mod.module_type, "title": inc_mod.title,
                 "content_text": chunk, "url": inc_mod.url, "visibility": inc_mod.visibility or {}})

    if texts_to_embed:
        for i in range(0, len(texts_to_embed), 16):
            batch = texts_to_embed[i:i + 16]
            vectors = embedder.encode(batch).tolist()
            for j, vector in enumerate(vectors):
                meta = chunk_metadata[i + j]
                db.add(ModuleIndex(
                    moodle_id=meta["moodle_id"], course_id=data.course_id, module_type=meta["module_type"],
                    title=meta["title"], content_text=meta["content_text"], url=meta["url"],
                    visibility=meta["visibility"], embedding=vector
                ))
            db.commit()
    return {"status": "success"}


@app.post("/api/smart-search")
def smart_search(data: SmartSearchRequest, db: Session = Depends(get_db)):
    if db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count() == 0:
        return {"reply": "Курс еще не проиндексирован.", "targets": []}

    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course: return {"reply": "Курс не найден.", "targets": []}

    user_msg = safe_strip(data.message)
    viewer_role = data.viewer_role or "student"
    course_modules = build_course_modules(db_course, viewer_role)
    ontology = build_course_ontology(course_modules, data.deadlines, data.course_title or db_course.title)

    enriched_msg = enrich_query_with_history(user_msg, data.history)
    route = route_request(enriched_msg, ontology, bool(data.deadlines))

    debug_context = [
        {"title": "action", "text": route["action"], "score": 0},
        {"title": "router_query (expanded)", "text": route["query"], "score": 0},
        {"title": "enriched_input", "text": enriched_msg, "score": 0}
    ]

    execution: Dict[str, Any] = {"facts": {}, "targets": []}

    if route["action"] == "teacher_info":
        # БЕРЕМ ИНФОРМАЦИЮ ИЗ БАЗЫ ДАННЫХ
        teachers_from_db = db.query(CourseParticipant).filter(
            CourseParticipant.course_id == data.course_id,
            CourseParticipant.role.ilike("%преподаватель%")
        ).all()

        if teachers_from_db:
            names = [p.name for p in teachers_from_db]
            execution["facts"]["преподаватели"] = "Преподаватели курса: " + ", ".join(names)
        else:
            execution["facts"]["преподаватели"] = data.teachers or "Информация о преподавателях не указана."

    elif route["action"] == "find_deadline":
        execution = exec_deadlines(data.deadlines, "generic")
    elif route["action"] == "course_overview":
        execution = exec_course_overview(ontology)
    elif route["action"] == "navigate":
        execution = exec_navigation(course_modules, data.history)
    else:
        execution = exec_answer_from_context(db, data.course_id, viewer_role, route["query"])

    if data.grades: execution["facts"]["оценки_студента"] = data.grades
    if data.assign_status: execution["facts"]["статус_задания"] = data.assign_status
    if data.teachers and route["action"] != "teacher_info": execution["facts"]["преподаватели_курса"] = data.teachers

    if execution.get("facts", {}).get("candidates"):
        for idx, c in enumerate(execution["facts"]["candidates"][:3], start=1):
            score_val = execution.get("score_map", {}).get(c["id"], 0)
            debug_context.append(
                {"title": f"{idx}. {c['title']}", "text": c["content_text"][:180] + "...", "score": score_val})

    response_data = generate_response(
        user_msg=user_msg,
        history=data.history,
        action=route["action"],
        execution=execution,
        ontology=ontology
    )

    targets = [{"id": t["moodle_id"], "url": t["url"], "title": t["title"], "snippet": t.get("snippet", "")} for t in
               response_data.get("targets", []) if t]

    try:
        new_log = ChatLog(
            course_id=data.course_id, viewer_role=viewer_role, user_query=data.message, ai_reply=response_data["reply"],
            used_context=f"action={route['action']}\nquery={route['query']}\n"
        )
        db.add(new_log)
        db.commit()
    except Exception:
        pass

    return {
        "reply": response_data["reply"],
        "targets": targets,
        "debug_context": debug_context,
        "debug_meta": {"action": route["action"], "router_query": route["query"]}
    }


@app.post("/api/feedback")
def save_feedback(req: FeedbackRequest, db: Session = Depends(get_db)):
    try:
        log_entry = db.query(ChatLog).filter(ChatLog.id == req.log_id).first()
        if log_entry:
            log_entry.is_helpful = bool(req.is_helpful)
            db.commit()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)