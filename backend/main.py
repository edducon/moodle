import re
import json
import copy
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from config import settings
from database import SessionLocal, Course, ModuleIndex, ChatLog, CourseParticipant, CourseDeadline
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

class SemanticRouter:
    def __init__(self, embedder):
        self.embedder = embedder

        self.intent_examples = {
            "teacher_info": [
                "кто преподаватель", "кто ведет курс", "контакты преподавателя",
                "как связаться с преподавателем", "кто лектор", "чья это дисциплина",
                "имя преподавателя", "почта преподавателя", "кто читает лекции",
                "кто препод", "как зовут препода", "препод", "преподаватель"
            ],
            "navigate": [
                "с чего начать", "что делать первым", "куда идти",
                "первое задание", "как начать курс", "какая первая тема",
                "что дальше", "а дальше", "следующее задание", "следующая тема",
                "что после этого", "куда идти после лекции", "что после лекции",
                "следующий шаг", "продолжить курс", "что делать потом",
                "куда идти после", "что после задания", "открой", "перейди",
                "я имел в виду теорию", "хочу начать с теории", "давай теорию",
                "где лекции", "перейти к лекции", "покажи теорию",
                "я имел в виду практику", "давай практику", "где задания"
            ],
            "grading": [
                "как получить оценку", "система оценивания", "критерии оценки",
                "как сдать экзамен", "как получить зачет", "баллы за задания",
                "как получить 5", "что будет на экзамене",
                "как получить пятерку", "как получить пятерочку", "получить хорошую оценку",
                "что нужно для отличника", "минимум для зачета",
                "система оценки дисциплины", "итоговая оценка за курс",
                "условия получения отличной оценки", "требования для получения пятерки",
                "условия получения зачтено", "критерии выставления оценки",
                "оценка без экзамена", "результаты семестровой работы", "порядок выставления итоговой оценки",
                "как получить автомат", "автоматическая оценка", "автомат по предмету",
                "условия зачета", "условия экзамена", "сколько баллов нужно",
                "сколько баллов за лабу", "баллы за лабораторную", "что нужно для пятерки"
            ],
            "find_deadline": [
                "когда сдавать", "дедлайн", "срок сдачи", "до какого числа",
                "горят сроки", "какие ближайшие дедлайны",
                "ближайший дедлайн", "какой дедлайн", "следующий дедлайн",
                "когда сдавать лабу", "когда сдавать задание", "срок сдачи задания",
                "до какого числа задание", "до какого числа лабу",
                "когда дедлайн по практическому", "срок сдачи практического занятия"
            ],
            "course_overview": [
                "из чего состоит курс", "что в курсе", "структура курса",
                "сколько заданий в курсе", "о чем этот предмет",
                "сколько лекций в курсе", "сколько лабораторных в курсе",
                "сколько практических занятий в курсе", "сколько всего заданий на курсе",
                "содержание курса", "план курса", "какие темы в курсе",
                "перечень модулей курса", "обзор курса"
            ],
            "smalltalk": [
                "привет", "здравствуй", "как дела", "кто ты", "спасибо",
                "понятно", "ок", "окей", "да", "нет", "зайка", "схерали",
                "круто", "ясно", "ага", "хорошо", "помоги", "че каво"
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

    def classify(self, query: str, threshold: float = 0.60) -> Tuple[str, str]:
        q_vec = self.embedder.encode([query])[0]

        similarities = cosine_similarity([q_vec], self.vectors)[0]
        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]

        if best_score >= threshold:
            best_intent = self.labels[best_idx]

            if best_intent == "smalltalk":
                return "smalltalk", query

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

ANAPHORA_MARKERS = [
    "а дальше", "что потом", "после этого", "а после",
    "ее", "её", "его", "их", "это", "эту", "этот", "этом", "там",
    "а как", "как это", "а она", "а оно", "он", "она", "они", "туда",
    "дальше", "следующее", "следующий", "потом", "после",
    "ещё где", "где ещё", "а ещё", "ещё раз", "откуда", "где нашел"
]

SELF_CONTAINED_MARKERS = [
    "что такое", "как работает", "почему", "зачем", "когда", "где находится",
    "нужно ли", "обязательно ли", "можно ли", "стоит ли"
]

ERROR_MARKERS = [
    "техническая ошибка", "пожалуйста, попробуйте", "произошла ошибка",
    "не проиндексирован", "не найден", "попробуйте спросить",
    "в материалах курса нет"
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

def split_text_into_chunks(text: str, max_chunk_size: int = 1200) -> List[str]:
    if not text: return []

    text = re.sub(r"Печатать книгу.*?Оглавление", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"Просмотр всех ответов[\s\S]*?(?=МЕТАДАННЫЕ|$)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{3}-\d{3,4}\b\s*", "", text)

    metadata_block = ""
    meta_marker = "МЕТАДАННЫЕ ЭЛЕМЕНТА:"
    if meta_marker in text:
        parts = text.split(meta_marker)
        text = parts[0]
        metadata_block = meta_marker + parts[1]

    if bool(re.search(r'</?(p|div|br|li|h[1-6]|table|tr|td)[^>]*>', text, re.IGNORECASE)):
        text = re.sub(r'<(p|div|br|li|h[1-6]|tr|table|ul|ol)[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</(p|div|li|h[1-6]|tr|table|ul|ol)>', '\n', text, flags=re.IGNORECASE)
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text(separator=" ")

    text = re.sub(r'([a-zа-яё])([.!?])([A-ZА-ЯЁ])', r'\1\2 \3', text)
    text = re.sub(r'([a-zа-яё])([A-ZА-ЯЁ])', r'\1 \2', text)
    text = re.sub(r'([.!?|)])\s*([А-ЯЁ][а-яёa-zA-Z\s,()]+[-–—−]\s)', r'\1\n\2', text)

    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s+', '\n', text)
    text = re.sub(r'\n+', '\n', text)

    paragraphs = text.strip().split('\n')
    chunks = []

    for p in paragraphs:
        p = p.strip()
        if len(p) < 15:
            continue

        if len(p) <= max_chunk_size:
            chunks.append(p)
        else:
            sentences = re.split(r'(?<=[.!?])\s+', p)
            current_chunk = ""

            for sentence in sentences:
                if len(sentence) > max_chunk_size:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""

                    words = sentence.split()
                    for word in words:
                        if len(current_chunk) + len(word) < max_chunk_size:
                            current_chunk += word + " "
                        else:
                            chunks.append(current_chunk.strip())
                            current_chunk = word + " "
                    continue

                if len(current_chunk) + len(sentence) <= max_chunk_size:
                    current_chunk += sentence + " "
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence + " "

            if current_chunk:
                chunks.append(current_chunk.strip())

    if metadata_block:
        chunks.append(metadata_block.strip())

    return chunks

def module_kind(title: str, module_type: Optional[str]) -> str:
    t, mt = normalize_text(title), normalize_text(module_type or "")

    if "экзамен" in t or "тест" in t: return "quiz"
    if "лаборатор" in t or "практическ" in t or "задани" in t or "отчет" in t: return "assignment"
    if "форум" in t or "обсужд" in t: return "forum"
    if "лекци" in t or "вводн" in t or "литератур" in t: return "learning"

    if mt == "quiz": return "quiz"
    if mt in ("assign", "workshop"): return "assignment"
    if mt == "forum": return "forum"
    if mt in ("page", "book", "file", "folder", "url", "lesson", "resource"): return "learning"

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

        m = re.search(r'(?:Переход|Источник):\s*(.+)', text, flags=re.IGNORECASE)
        if m: return m.group(1).strip()

        m = re.search(r'["«](.+?)["»]', text)
        if m: return m.group(1).strip()

        m = re.search(r"откройте\s+(?:сначала\s+)?(.+?)(?:\.|$)", text, flags=re.IGNORECASE)
        if m: return m.group(1).strip()

        m = re.search(
            r"(лекци[ия]\s*№?\s*\d+|лаборатор\w+\s*работ\w*\s*№?\s*\d+|практическ\w+\s*\w*\s*№?\s*\d+|тем[аы]\s*№?\s*\d+|модуль\s*№?\s*\d+|раздел\s*№?\s*\d+)",
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

def choose_default_start(course_modules: List[Dict[str, Any]], skip_org: bool = False) -> Optional[Dict[str, Any]]:
    ordered = order_modules(course_modules)
    if not ordered: return None

    stop_words = ["вводн", "информаци", "описание дисциплин", "план", "литератур", "ресурс", "справочн", "глоссарий",
                  "пример", "тренировочн", "дополнительн"]

    def score(m: Dict[str, Any]) -> Tuple[int, Tuple[int, int, int], int, int]:
        title = normalize_text(m["title"])
        bias = 0

        if skip_org and any(sw in title for sw in stop_words):
            bias += 1000

        if not skip_org:
            if "вводн" in title:
                bias -= 100
            elif "описание дисциплин" in title or "информаци" in title:
                bias -= 80

        if m["kind"] == "learning":
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
        "start_module": choose_default_start(course_modules, skip_org=False),
        "deadlines": deadlines,
    }

def route_request(enriched_msg: str, ontology: Dict[str, Any], has_deadlines: bool) -> Dict[str, Any]:
    msg_normalized = normalize_text(enriched_msg)
    words = msg_normalized.split()

    whitelist_markers = [
        "дедлайн", "лекци", "задани", "тест", "оценк", "препод", "начать",
        "экзамен", "автомат", "зачет", "сдавать", "баллы", "правил", "курс", "срок",
        "лаб", "практическ", "сколько", "структур", "условия", "дальше", "следующ", "преподавател",
        "теори", "материал", "потом", "после"
    ]
    if len(words) <= 2 and not any(marker in msg_normalized for marker in whitelist_markers):
        return {"action": "smalltalk", "scope": "generic", "query": enriched_msg, "original_intent": "smalltalk"}

    teacher_keywords = ["препод", "преподавател", "лектор", "ведет курс", "кто читает"]
    if any(kw in msg_normalized for kw in teacher_keywords):
        return {"action": "teacher_info", "scope": "generic", "query": enriched_msg, "original_intent": "teacher_info"}

    intent, search_query = semantic_router.classify(enriched_msg, threshold=0.60)

    grading_keywords = ["оценк", "сда", "балл", "зачет", "экзамен", "автомат", "дедлайн", "система", "критерии"]

    is_false_positive = (intent in ["grading", "find_deadline"]) and not any(
        kw in msg_normalized for kw in grading_keywords)

    is_term_query = len(words) <= 6 and intent not in ["navigate", "smalltalk", "teacher_info", "course_overview",
                                                       "find_deadline"] and not any(
        kw in msg_normalized for kw in grading_keywords)

    if is_false_positive or is_term_query:
        intent = "answer_from_context"
        search_query = enriched_msg

    route = {"action": intent, "scope": "generic", "query": search_query, "original_intent": intent}

    if intent == "grading":
        route["action"] = "answer_from_context"

    return route


def retrieve_candidates(db: Session, course_id: str, viewer_role: str, search_query: str) -> Tuple[
    List[Dict[str, Any]], Dict[str, float]]:
    query_vector = embedder.encode([search_query])[0].tolist()
    distance_col = ModuleIndex.embedding.cosine_distance(query_vector)

    raw_results = db.query(ModuleIndex, distance_col.label("distance")).filter(
        ModuleIndex.course_id == course_id
    ).order_by(distance_col).limit(20).all()

    query_normalized = normalize_text(search_query)
    stop_words = {"такое", "какой", "какая", "какие", "этот", "этого", "через",
                  "можно", "нужно", "будет", "может", "когда", "после", "перед",
                  "между", "около", "более", "менее", "очень", "также", "только",
                  "получить", "знать", "хочу", "курсу", "делать", "какую"}
    query_keywords = [w for w in query_normalized.split() if len(w) > 3 and w not in stop_words]

    scored = []
    for c, dist in raw_results:
        if not db_module_visible_for_role(c, viewer_role): continue

        content_normalized = normalize_text(c.content_text or "")
        keyword_boost = 0.0
        for kw in query_keywords:
            if kw in content_normalized:
                keyword_boost += 0.06

        keyword_boost = min(keyword_boost, 0.15)
        adjusted_dist = max(0.01, dist - keyword_boost)

        scored.append((adjusted_dist, {
            "id": str(c.id), "moodle_id": c.moodle_id, "url": c.url,
            "title": c.title, "kind": module_kind(c.title or "", c.module_type),
            "content_text": c.content_text, "module_type": c.module_type,
        }))

    scored.sort(key=lambda x: x[0])
    candidates = [item for _, item in scored[:8]]
    score_map = {item["id"]: round(score, 4) for score, item in scored[:15]}
    return candidates, score_map

def exec_navigation(course_modules: List[Dict[str, Any]], history: List[ChatHistoryItem], query: str = "") -> Dict[
    str, Any]:
    last_target = extract_last_bot_navigation_target(history)
    query_lower = normalize_text(query)

    def clean_mod(m: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not m: return None
        c = m.copy()
        c.pop("url", None)
        return c

    is_next = any(w in query_lower for w in ["дальше", "потом", "следующ", "после"])
    if is_next and last_target:
        nxt = get_next_module_after(course_modules, last_target)
        if nxt: return {"facts": {"mode": "next_step", "current_target": last_target, "next_module": clean_mod(nxt)},
                        "targets": [nxt]}

    stop_words = ["вводн", "информаци", "план", "литератур", "ресурс", "справочн", "глоссарий", "пример", "зачет",
                  "тренировочн", "дополнительн"]

    if any(w in query_lower for w in ["задани", "лаб", "практическ", "практик", "тест"]):
        assigns = [m for m in order_modules(course_modules) if m["kind"] in ("assignment", "quiz") and not any(
            sw in normalize_text(m["title"]) for sw in stop_words)]
        if not assigns:
            assigns = [m for m in order_modules(course_modules) if m["kind"] in ("assignment", "quiz")]
        if assigns:
            start = assigns[0]
        else:
            return {"facts": {"mode": "no_assignments",
                              "указание_ии": "На данный момент нет доступных практических заданий или лабораторных работ. Возможно, они откроются позже или доступны только при выполнении определённых условий."},
                    "targets": []}

    elif any(w in query_lower for w in ["теори", "лекци", "учебник", "читать", "материал"]):
        learning = [m for m in order_modules(course_modules) if m["kind"] == "learning"]
        real_theory = [m for m in learning if not any(sw in normalize_text(m["title"]) for sw in stop_words)]
        if not real_theory:
            real_theory = learning
        start = real_theory[0] if real_theory else choose_default_start(course_modules, skip_org=True)

    else:
        start = choose_default_start(course_modules, skip_org=False)

    return {"facts": {"mode": "start", "start_module": clean_mod(start)}, "targets": [start] if start else []}

def exec_course_overview(ontology: Dict[str, Any], course_modules: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = order_modules(course_modules)
    learning_titles = [m["title"] for m in ordered if m["kind"] == "learning"][:10]
    assignment_titles = [m["title"] for m in ordered if m["kind"] == "assignment"][:10]
    quiz_titles = [m["title"] for m in ordered if m["kind"] == "quiz"][:10]

    start_mod = ontology.get("start_module")
    if start_mod:
        start_mod = start_mod.copy()
        start_mod.pop("url", None)

    return {"facts": {
        "course_title": ontology.get("course_title", ""),
        "counts": ontology.get("counts", {}),
        "start_module": start_mod,
        "learning_modules": learning_titles,
        "assignment_modules": assignment_titles,
        "quiz_modules": quiz_titles,
    }, "targets": [ontology.get("start_module")] if ontology.get("start_module") else []}


def exec_deadlines(deadlines: List[DeadlineItem], scope: str) -> Dict[str, Any]:
    if not deadlines:
        return {"facts": {
            "has_deadlines": False,
            "deadlines": [],
            "scope": scope,
            "указание_ии": "На этом курсе не установлены конкретные сроки сдачи заданий в системе, либо все задания уже сданы. Рекомендуй студенту обратиться к преподавателю за информацией о сроках."
        }, "targets": []}
    return {"facts": {"has_deadlines": True, "deadlines": deadlines[:10], "scope": scope}, "targets": []}


def exec_answer_from_context(db: Session, course_id: str, viewer_role: str, query: str, intent: str = "") -> Dict[
    str, Any]:
    candidates, score_map = retrieve_candidates(db=db, course_id=course_id, viewer_role=viewer_role, search_query=query)

    if intent == "grading":
        grading_title_keywords = ["оценк", "оценивани", "система оценки", "аттестац", "зачет", "экзамен"]

        grading_modules = db.query(ModuleIndex).filter(
            ModuleIndex.course_id == course_id
        ).all()

        existing_ids = {c["id"] for c in candidates}
        grading_extra = []
        for m in grading_modules:
            if str(m.id) in existing_ids: continue
            if not db_module_visible_for_role(m, viewer_role): continue
            title_norm = normalize_text(m.title or "")
            content_norm = normalize_text(m.content_text or "")
            if any(kw in title_norm for kw in grading_title_keywords) or \
                    any(kw in content_norm for kw in ["зачтено", "неудовлетвор", "отлично", "хорошо", "удовлетвор",
                                                      "итоговая оценка", "балл", "промежуточн"]):
                grading_extra.append({
                    "id": str(m.id), "moodle_id": m.moodle_id, "url": m.url,
                    "title": m.title, "kind": module_kind(m.title or "", m.module_type),
                    "content_text": m.content_text, "module_type": m.module_type,
                })
                if len(grading_extra) >= 3: break

        grading_candidates = [c for c in candidates if
                              any(kw in normalize_text(c.get("title", "")) for kw in grading_title_keywords)]
        other_candidates = [c for c in candidates if c not in grading_candidates]
        candidates = grading_extra + grading_candidates + other_candidates

    for c in candidates:
        snippet_raw = c.get("content_text", "")[:80].strip()
        c["snippet"] = re.sub(r'\s+', ' ', snippet_raw)

    facts_candidates = []
    for c in candidates[:5]:
        c_copy = c.copy()
        c_copy.pop("url", None)
        facts_candidates.append(c_copy)

    return {"facts": {"query": query, "candidates": facts_candidates}, "targets": candidates[:5],
            "score_map": score_map}

SYSTEM_PROMPT = """Ты помощник по учебному курсу в Moodle. Отвечай на основе предоставленных материалов курса.

Правила:
- Отвечай уверенно и кратко, 2-4 предложения.
- Запрещены слова: "К сожалению", "Извините", "Прошу прощения".
- Если в материалах ЕСТЬ информация по теме вопроса — ответь на её основе, даже если точная формулировка отличается от вопроса.
- НЕ ПРИДУМЫВАЙ дедлайны, оценки, правила сдачи, даты, количество попыток — это критически важно.
- Если источник содержит ТОЛЬКО метаданные (условия завершения, раздел курса) БЕЗ описания — НЕ ВЫДУМЫВАЙ процедуру. Скажи что есть и дай ссылку.
- "Экзамен", "зачет", "аттестация" на курсе — это обычно прохождение итогового тестирования. Если студент спрашивает про экзамен, ищи информацию про итоговое тестирование и систему оценки.
- Если информации действительно нет НИ В ОДНОМ источнике — скажи "В материалах курса нет информации по этому вопросу. Рекомендую обратиться к преподавателю."

Формат ответа — строго JSON:
{"reply": "твой ответ студенту", "show_link": true, "source_id": "id источника"}

ВАЖНО: В поле "reply" НИКОГДА не включай технические ID источников (src_1, src_2 и т.д.). ID указывай ТОЛЬКО в поле "source_id".

Когда show_link = true:
- Запрос определения ("Что такое...", "Кто такой...")
- Студент просит ссылку или переход к материалу
- Ответ основан на конкретном источнике
В остальных случаях show_link = false, source_id = null."""


def format_context_for_llm(facts: dict) -> Tuple[str, Dict[str, str]]:
    parts = []
    id_map = {}

    candidates = facts.get("candidates", [])
    if candidates:
        parts.append("НАЙДЕННЫЕ МАТЕРИАЛЫ КУРСА:")
        for i, c in enumerate(candidates, 1):
            title = c.get("title", "Без названия")
            text = c.get("content_text", "").strip()
            cid = c.get("id", "")
            simple_id = f"src_{i}"
            id_map[simple_id] = cid
            if text:
                parts.append(f"\n[Источник {i}] (id: {simple_id}) {title}\n{text}")

    skip_keys = {"query", "candidates"}
    extra_facts = {k: v for k, v in facts.items() if k not in skip_keys and v}
    if extra_facts:
        parts.append("\nДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ:")
        parts.append(json.dumps(extra_facts, ensure_ascii=False, default=str))

    return ("\n".join(parts) if parts else "Релевантных материалов не найдено."), id_map


def clean_reply_text(reply: str) -> str:
    reply = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '', reply)
    reply = re.sub(r'\bsrc_\d+\b', '', reply)
    reply = re.sub(r'в источнике\s*[,.]?\s*', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'источник\s*[,.]?\s*', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'\s+', ' ', reply).strip()
    return reply


def generate_response(
        user_msg: str,
        history: List[ChatHistoryItem],
        action: str,
        execution: Dict[str, Any],
        ontology: Dict[str, Any]
) -> Dict[str, Any]:
    facts = execution.get("facts", {})
    all_targets = [t for t in execution.get("targets", []) if t]

    context_text, id_map = format_context_for_llm(facts)

    user_prompt = f"""Курс: {ontology.get("course_title", "Без названия")}
Вопрос студента: {user_msg}

{context_text}"""

    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
            extra_body={"options": {"num_ctx": 12288}}
        )

        raw_content = resp.choices[0].message.content.strip()
        data = extract_json_from_text(raw_content)
        reply = clean_reply_text(safe_strip(data.get("reply", "")))

        show_link_val = data.get("show_link", False)
        show_link = str(show_link_val).lower() == "true" if isinstance(show_link_val, str) else bool(show_link_val)

        source_id_raw = data.get("source_id") or ""
        source_id = id_map.get(source_id_raw, source_id_raw)
        exact_quote = data.get("exact_quote")

        final_targets = []
        if action == "navigate" or action == "course_overview":
            show_link = True
            final_targets = all_targets
        elif show_link:
            if source_id:
                final_targets = [t for t in all_targets if str(t.get("id")) == str(source_id)]
            if not final_targets and all_targets:
                final_targets = [all_targets[0]]

            if exact_quote and final_targets:
                final_targets[0]["snippet"] = exact_quote

        if not reply and raw_content:
            if not raw_content.startswith("{"):
                reply = raw_content

        if reply:
            return {"reply": reply, "targets": final_targets if show_link else []}

    except Exception as e:
        print(f"Ошибка LLM-генерации: {e}")
        if 'resp' in locals() and resp.choices:
            raw = resp.choices[0].message.content.strip()
            recovered = extract_json_from_text(raw)
            if recovered and recovered.get("reply"):
                reply = clean_reply_text(recovered["reply"])
                show_link_val = recovered.get("show_link", False)
                show_link = str(show_link_val).lower() == "true" if isinstance(show_link_val, str) else bool(
                    show_link_val)

                source_id_raw = recovered.get("source_id") or ""
                source_id = id_map.get(source_id_raw, source_id_raw)
                exact_quote = recovered.get("exact_quote")

                final_targets = []
                if action == "navigate" or action == "course_overview":
                    show_link = True
                    final_targets = all_targets
                elif show_link:
                    if source_id:
                        final_targets = [t for t in all_targets if str(t.get("id")) == str(source_id)]
                    if not final_targets and all_targets:
                        final_targets = [all_targets[0]]

                    if exact_quote and final_targets:
                        final_targets[0]["snippet"] = exact_quote

                return {"reply": reply, "targets": final_targets if show_link else []}
            if not raw.startswith("{"):
                return {"reply": clean_reply_text(raw), "targets": all_targets[:1]}

    return {
        "reply": "Произошла техническая ошибка при формулировании ответа. Пожалуйста, попробуйте спросить чуть иначе.",
        "targets": all_targets[:1]}

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

    if data.participants and len(data.participants) > 0:
        valid_participants = [
            p for p in data.participants
            if p.get("role", "").strip()
        ]
        if valid_participants:
            db.query(CourseParticipant).filter(CourseParticipant.course_id == data.course_id).delete()
            for p in valid_participants:
                db.add(CourseParticipant(
                    course_id=data.course_id,
                    name=p.get("name", ""),
                    role=p.get("role", "Преподаватель"),
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

    deadlines = data.deadlines
    if deadlines:
        try:
            db.query(CourseDeadline).filter(CourseDeadline.course_id == data.course_id).delete()
            for d in deadlines:
                db.add(CourseDeadline(
                    course_id=data.course_id,
                    moodle_id="",
                    title=d.title,
                    due_date=d.due_date,
                    url=d.url
                ))
            db.commit()
        except Exception:
            pass
    else:
        db_deadlines = db.query(CourseDeadline).filter(
            CourseDeadline.course_id == data.course_id
        ).all()
        if db_deadlines:
            deadlines = [DeadlineItem(title=d.title, due_date=d.due_date, url=d.url) for d in db_deadlines]

    ontology = build_course_ontology(course_modules, deadlines, data.course_title or db_course.title)

    enriched_msg = enrich_query_with_history(user_msg, data.history)
    route = route_request(enriched_msg, ontology, bool(deadlines))

    msg_normalized = normalize_text(user_msg)
    words = msg_normalized.split()
    source_request_markers = ["откуда", "где ты", "где нашел", "каком материал", "какой лекции", "где это",
                              "каком модуле", "источник", "ссылку", "ссылка"]
    is_source_request = len(words) <= 7 and any(m in msg_normalized for m in source_request_markers)

    if is_source_request:
        last_target = extract_last_bot_navigation_target(data.history)
        if last_target:
            route["query"] = last_target
            route["action"] = "answer_from_context"
            route["original_intent"] = "answer_from_context"

    debug_context = [
        {"title": "action", "text": route["action"], "score": 0},
        {"title": "router_query (expanded)", "text": route["query"], "score": 0},
        {"title": "enriched_input", "text": enriched_msg, "score": 0}
    ]

    execution: Dict[str, Any] = {"facts": {}, "targets": []}

    if route["action"] == "smalltalk":
        execution["facts"][
            "указание_ии"] = "Это неформальный, бессмысленный запрос или приветствие. Поздоровайся, если это приветствие, или просто вежливо скажи, что ты помощник по курсу, и предложи задать конкретный вопрос по учебе."
    elif route["action"] == "teacher_info":
        teachers_from_db = db.query(CourseParticipant).filter(
            CourseParticipant.course_id == data.course_id,
            CourseParticipant.role != ""
        ).all()

        if teachers_from_db:
            teacher_lines = [f"{p.name} ({p.role})" if p.role else p.name for p in teachers_from_db]
            execution["facts"]["преподаватели"] = "Преподаватели курса: " + ", ".join(teacher_lines)
        else:
            fallback_teachers = data.teachers.strip() if data.teachers else ""
            execution["facts"][
                "преподаватели"] = fallback_teachers if fallback_teachers else "В системе нет информации о преподавателях этого курса."

    elif route["action"] == "find_deadline":
        execution = exec_deadlines(deadlines, "generic")
    elif route["action"] == "course_overview":
        execution = exec_course_overview(ontology, course_modules)
    elif route["action"] == "navigate":
        execution = exec_navigation(course_modules, data.history, route["query"])
    else:
        actual_intent = route.get("original_intent", route["action"])
        execution = exec_answer_from_context(db, data.course_id, viewer_role, route["query"], intent=actual_intent)

    if data.grades: execution["facts"]["оценки_студента"] = data.grades
    if data.assign_status: execution["facts"]["статус_задания"] = data.assign_status

    if data.teachers and route["action"] != "teacher_info" and "преподаватели" not in execution["facts"]:
        execution["facts"]["преподаватели_курса"] = data.teachers

    if execution.get("facts", {}).get("candidates"):
        for idx, c in enumerate(execution["facts"]["candidates"][:5], start=1):
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
        "debug_meta": {"action": route["action"], "router_query": route["query"],
                       "original_intent": route.get("original_intent", "")}
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