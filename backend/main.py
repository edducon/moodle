import re
import json
import copy
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from config import settings
from database import SessionLocal, Course, ModuleIndex, ChatLog
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
# SCHEMAS
# =========================

class CourseData(BaseModel):
    course_id: str
    title: str
    sections: List[Dict[str, Any]]
    viewer_role: Optional[str] = None


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
# HELPERS
# =========================

def normalize_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text


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
                raw = raw.replace("\n", " ").replace("\r", "")
                return json.loads(raw)
    except Exception:
        pass
    return {}


def db_module_visible_for_role(mod: ModuleIndex, viewer_role: Optional[str]) -> bool:
    visibility = mod.visibility or {}
    if viewer_role == "teacher":
        return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False):
        return False
    return True


def course_module_visible_for_role(module_data: Dict[str, Any], viewer_role: Optional[str]) -> bool:
    visibility = module_data.get("visibility") or {}
    if viewer_role == "teacher":
        return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False):
        return False
    return True


def split_text_into_chunks(text: str, min_size: int = 200, max_size: int = 1500) -> List[str]:
    if not text:
        return []

    text = re.sub(r"Печатать книгу.*?Оглавление", "", text, flags=re.IGNORECASE | re.DOTALL)
    paragraphs = re.split(r"\n+", text.strip())

    chunks = []
    current_chunk = ""

    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 10:
            continue

        if len(current_chunk) + len(p) + 1 <= max_size:
            current_chunk += ("\n" + p if current_chunk else p)
        else:
            if len(current_chunk) >= min_size:
                chunks.append(current_chunk.strip())
            current_chunk = p

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def module_kind(title: str, module_type: Optional[str]) -> str:
    t = normalize_text(title)
    mt = normalize_text(module_type or "")

    if mt == "quiz":
        return "quiz"
    if mt in ("assign", "workshop"):
        return "assignment"
    if mt == "forum":
        return "forum"
    if mt in ("page", "book", "file", "folder", "url", "lesson", "resource"):
        return "learning"

    if "экзамен" in t or "тест" in t:
        return "quiz"
    if "лаборатор" in t or "практическ" in t or "задани" in t:
        return "assignment"
    if "форум" in t or "обсужд" in t:
        return "forum"
    if "лекци" in t or "вводн" in t or "литератур" in t:
        return "learning"

    return "other"


def parse_order_from_title(title: str) -> Tuple[int, int, int]:
    t = normalize_text(title)

    m = re.search(r"(\d+)\.(\d+)", t)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0)

    m = re.search(r"лекц\w*\s*№\s*(\d+)", t)
    if m:
        return (999, int(m.group(1)), 1)

    m = re.search(r"лаборат\w*\s*работ\w*\s*№\s*(\d+)", t)
    if m:
        return (999, int(m.group(1)), 2)

    m = re.search(r"практическ\w*\s*(?:заняти\w*|работ\w*)\s*№\s*(\d+)", t)
    if m:
        return (999, int(m.group(1)), 2)

    m = re.search(r"тест\w*\s*№\s*(\d+)", t)
    if m:
        return (999, int(m.group(1)), 3)

    return (9999, 9999, 9999)


def build_course_modules(db_course: Course, viewer_role: str) -> List[Dict[str, Any]]:
    result = []
    sections = db_course.content or []

    for s_idx, sec in enumerate(sections):
        for m_idx, mod in enumerate(sec.get("modules", [])):
            if not course_module_visible_for_role(mod, viewer_role):
                continue

            title = mod.get("title", "Без названия")
            module_type = mod.get("type")
            result.append({
                "moodle_id": mod.get("moodle_id"),
                "title": title,
                "url": mod.get("url", ""),
                "module_type": module_type,
                "kind": module_kind(title, module_type),
                "section_index": s_idx,
                "module_index": m_idx,
                "order_key": parse_order_from_title(title),
                "visibility": mod.get("visibility") or {}
            })

    return result


def order_modules(course_modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        course_modules,
        key=lambda m: (m["order_key"], m["section_index"], m["module_index"])
    )


def extract_last_user_message(history: List[ChatHistoryItem]) -> str:
    for item in reversed(history):
        if item.role == "user" and safe_strip(item.content):
            return safe_strip(item.content)
    return ""


def extract_last_bot_navigation_target(history: List[ChatHistoryItem]) -> str:
    for item in reversed(history):
        if item.role != "assistant":
            continue
        text = item.content or ""

        m = re.search(r'["«](.+?)["»]', text)
        if m:
            return m.group(1).strip()

        m = re.search(r"откройте\s+(?:сначала\s+)?(.+?)(?:\.|$)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

    return ""


def get_next_module_after(course_modules: List[Dict[str, Any]], current_title: str) -> Optional[Dict[str, Any]]:
    if not current_title:
        return None

    ordered = order_modules(course_modules)
    current_norm = normalize_text(current_title)

    idx = None
    for i, item in enumerate(ordered):
        if normalize_text(item["title"]) == current_norm:
            idx = i
            break

    if idx is None:
        return None

    if idx + 1 < len(ordered):
        return ordered[idx + 1]

    return None


def choose_default_start(course_modules: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ordered = order_modules(course_modules)
    if not ordered:
        return None

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


def build_course_ontology(course_modules: List[Dict[str, Any]], deadlines: List[DeadlineItem], course_title: str) -> Dict[str, Any]:
    ordered = order_modules(course_modules)

    learning = [m for m in ordered if m["kind"] == "learning"]
    assignments = [m for m in ordered if m["kind"] == "assignment"]
    quizzes = [m for m in ordered if m["kind"] == "quiz"]
    forums = [m for m in ordered if m["kind"] == "forum"]
    start_module = choose_default_start(course_modules)

    return {
        "course_title": course_title,
        "counts": {
            "all": len(ordered),
            "learning": len(learning),
            "assignments": len(assignments),
            "quizzes": len(quizzes),
            "forums": len(forums),
        },
        "start_module": start_module,
        "ordered_titles": [m["title"] for m in ordered[:30]],
        "deadlines": deadlines,
    }


# =========================
# RESTRICTIONS PARSER
# =========================

def restriction_type_from_text(text: str) -> str:
    t = normalize_text(text)

    if "с или после" in t or t.startswith("до ") or " до " in t:
        return "date"
    if "должен быть отмечен как выполненный" in t or "выполненн" in t:
        return "completion"
    if "больше необходимой оценки" in t or "проходной балл" in t or "оценк" in t:
        return "grade"
    if "провести за изучением курса" in t or "сек." in t or "секунд" in t:
        return "time_spent"

    return "other"


def parse_course_page_restrictions(page_html: str) -> List[Dict[str, Any]]:
    if not page_html or "<html" not in page_html.lower():
        return []

    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    seen = set()

    activity_nodes = soup.select("li.activity, li.activity-wrapper, li[id^='module-']")
    for node in activity_nodes:
        module_id = node.get("id", "").strip()

        title_tag = (
            node.select_one("[data-for='section_title']")
            or node.select_one(".activityname")
            or node.select_one(".instancename")
            or node.select_one("a[href*='/mod/']")
        )
        title = safe_strip(title_tag.get_text(" ", strip=True) if title_tag else "")

        restriction_box = node.select_one(".availabilityinfo.isrestricted")
        restrictions = []

        if restriction_box:
            lis = restriction_box.select("ul[data-region='availability-multiple'] li")
            if lis:
                for li in lis:
                    txt = safe_strip(li.get_text(" ", strip=True))
                    if txt:
                        restrictions.append({
                            "text": txt,
                            "type": restriction_type_from_text(txt)
                        })
            else:
                txt = safe_strip(restriction_box.get_text(" ", strip=True))
                if txt:
                    restrictions.append({
                        "text": txt,
                        "type": restriction_type_from_text(txt)
                    })

        key = (module_id, title)
        if key in seen:
            continue
        seen.add(key)

        if module_id or title or restrictions:
            results.append({
                "module_id": module_id,
                "title": title,
                "is_restricted": bool(restriction_box),
                "restrictions": restrictions
            })

    return results


# =========================
# ROUTER
# =========================

def route_request(
    user_msg: str,
    history: List[ChatHistoryItem],
    ontology: Dict[str, Any],
    has_deadlines: bool,
    has_page_context: bool
) -> Dict[str, Any]:
    history_text = "\n".join([f"{h.role}: {h.content}" for h in history[-6:]])
    last_user = extract_last_user_message(history[:-1]) if len(history) > 1 else ""
    last_nav_target = extract_last_bot_navigation_target(history)

    prompt = f"""
Ты роутер запросов для помощника по курсу.
Нельзя отвечать пользователю. Нужно только выбрать действие.

Верни СТРОГО JSON:
{{
  "action": "smalltalk | navigate | course_overview | count_items | find_deadline | check_requirement | answer_from_context | clarify",
  "scope": "course | learning | assignment | quiz | exam | grading | term | generic",
  "use_history": true,
  "use_retrieval": true,
  "use_structure": false,
  "use_deadlines": false,
  "use_restrictions": false,
  "query": "что искать по смыслу",
  "reason": "краткое техническое объяснение"
}}

Правила:
- smalltalk только если это в основном беседа, реакция или тональная реплика
- navigate если пользователь хочет понять с чего начать, что дальше, куда перейти
- course_overview если пользователь спрашивает о курсе в целом
- count_items если пользователь спрашивает сколько чего в курсе
- find_deadline если пользователь спрашивает про сроки и дедлайны
- check_requirement если пользователь спрашивает, обязательно ли что-то делать перед другим
- answer_from_context если нужен ответ по материалам курса
- clarify если пользователь оспаривает прошлый ответ или нужно переосмыслить предыдущий вопрос

Контекст:
course_title = {ontology.get("course_title", "")}
counts = {json.dumps(ontology.get("counts", {}), ensure_ascii=False)}
has_deadlines = {has_deadlines}
has_page_context = {has_page_context}
last_user_question = {last_user}
last_navigation_target = {last_nav_target}

История:
{history_text}

Текущий запрос:
{user_msg}
"""

    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        data = extract_json_from_text(resp.choices[0].message.content)
    except Exception:
        data = {}

    action = safe_strip(data.get("action", "answer_from_context")).lower()
    scope = safe_strip(data.get("scope", "generic")).lower()
    query = safe_strip(data.get("query", user_msg))
    reason = safe_strip(data.get("reason", "router_fallback"))

    valid_actions = {
        "smalltalk", "navigate", "course_overview", "count_items",
        "find_deadline", "check_requirement", "answer_from_context", "clarify"
    }
    valid_scopes = {"course", "learning", "assignment", "quiz", "exam", "grading", "term", "generic"}

    if action not in valid_actions:
        action = "answer_from_context"
    if scope not in valid_scopes:
        scope = "generic"

    return {
        "action": action,
        "scope": scope,
        "use_history": bool(data.get("use_history", True)),
        "use_retrieval": bool(data.get("use_retrieval", action in {"answer_from_context", "clarify"})),
        "use_structure": bool(data.get("use_structure", action in {"navigate", "course_overview", "count_items"})),
        "use_deadlines": bool(data.get("use_deadlines", action == "find_deadline")),
        "use_restrictions": bool(data.get("use_restrictions", action == "check_requirement")),
        "query": query or user_msg,
        "reason": reason or "router_fallback",
    }


# =========================
# RETRIEVAL
# =========================

def build_search_query(user_msg: str, history: List[ChatHistoryItem], routed_query: str) -> str:
    history_text = "\n".join([f"{h.role}: {h.content}" for h in history[-4:]])

    prompt = f"""
Собери короткий поисковый запрос по материалам курса.
Верни только JSON:

{{
  "search_query": "..."
}}

История:
{history_text}

Текущий вопрос:
{user_msg}

Смысл вопроса:
{routed_query}
"""
    try:
        r = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        data = extract_json_from_text(r.choices[0].message.content)
        return safe_strip(data.get("search_query", routed_query or user_msg)) or user_msg
    except Exception:
        return routed_query or user_msg


def retrieve_candidates(
    db: Session,
    course_id: str,
    viewer_role: str,
    search_query: str,
    scope: str,
    user_msg: str
) -> Tuple[List[Dict[str, Any]], Dict[int, float]]:
    search_query_norm = normalize_text(search_query)
    keywords = set(re.sub(r"[^\w\s-]", "", search_query_norm).split())

    query_vector = embedder.encode([search_query])[0].tolist()
    distance_col = ModuleIndex.embedding.cosine_distance(query_vector)

    raw_results = db.query(ModuleIndex, distance_col.label("distance")).filter(
        ModuleIndex.course_id == course_id
    ).order_by(distance_col).limit(120).all()

    scored = []

    for c, dist in raw_results:
        if not db_module_visible_for_role(c, viewer_role):
            continue

        title_lower = normalize_text(c.title or "")
        text_lower = normalize_text(c.content_text or "")
        kind = module_kind(c.title or "", c.module_type)

        bonus = 0.0
        penalty = 0.0

        for kw in keywords:
            if len(kw) < 4:
                continue
            root = kw[:-2] if len(kw) > 4 else kw
            if root in title_lower:
                bonus += 0.18
            if root in text_lower:
                bonus += 0.10

        # Generic scope biases, not phrase dictionaries.
        if scope == "term":
            if kind == "learning":
                bonus += 0.60
            if kind in {"forum"}:
                penalty += 0.70
            if "система оценки" in title_lower:
                penalty += 0.80

        elif scope == "grading":
            if "оценк" in title_lower or "балл" in text_lower or "зач" in text_lower or "экзам" in text_lower:
                bonus += 0.45
            if kind == "forum":
                penalty += 0.35

        elif scope == "assignment":
            if kind == "assignment":
                bonus += 0.60
            if kind == "forum":
                penalty += 0.90
            if kind == "quiz":
                penalty += 0.70

        elif scope == "learning":
            if kind == "learning":
                bonus += 0.45
            if kind == "forum":
                penalty += 0.40

        elif scope == "exam":
            if "экзам" in title_lower or ("итог" in title_lower and kind == "quiz"):
                bonus += 0.75

        final_score = dist - bonus + penalty

        scored.append((final_score, {
            "id": c.id,
            "moodle_id": c.moodle_id,
            "url": c.url,
            "title": c.title,
            "kind": kind,
            "content_text": c.content_text,
            "module_type": c.module_type,
        }))

    scored.sort(key=lambda x: x[0])
    candidates = [item for _, item in scored[:8]]
    score_map = {item["id"]: round(score, 4) for score, item in scored[:20]}
    return candidates, score_map


# =========================
# EXECUTORS
# =========================

def exec_navigation(course_modules: List[Dict[str, Any]], history: List[ChatHistoryItem]) -> Dict[str, Any]:
    last_target = extract_last_bot_navigation_target(history)
    if last_target:
        nxt = get_next_module_after(course_modules, last_target)
        if nxt:
            return {
                "facts": {
                    "mode": "next_step",
                    "current_target": last_target,
                    "next_module": nxt
                },
                "targets": [nxt]
            }

    start = choose_default_start(course_modules)
    return {
        "facts": {
            "mode": "start",
            "start_module": start
        },
        "targets": [start] if start else []
    }


def exec_course_overview(ontology: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "facts": {
            "course_title": ontology.get("course_title", ""),
            "counts": ontology.get("counts", {}),
            "ordered_titles": ontology.get("ordered_titles", [])[:8],
            "start_module": ontology.get("start_module")
        },
        "targets": [ontology.get("start_module")] if ontology.get("start_module") else []
    }


def exec_count_items(course_modules: List[Dict[str, Any]], scope: str) -> Dict[str, Any]:
    if scope == "assignment":
        items = [m for m in course_modules if m["kind"] == "assignment"]
    elif scope == "learning":
        items = [m for m in course_modules if m["kind"] == "learning"]
    elif scope == "quiz":
        items = [m for m in course_modules if m["kind"] == "quiz"]
    else:
        items = course_modules

    return {
        "facts": {
            "scope": scope,
            "count": len(items),
            "sample_titles": [m["title"] for m in items[:10]]
        },
        "targets": []
    }


def exec_deadlines(deadlines: List[DeadlineItem], scope: str, user_msg: str) -> Dict[str, Any]:
    if not deadlines:
        return {
            "facts": {
                "has_deadlines": False,
                "deadlines": []
            },
            "targets": []
        }

    filtered = deadlines
    if scope == "assignment":
        filtered = [
            d for d in deadlines
            if "лаб" in normalize_text(d.title) or "практическ" in normalize_text(d.title)
        ] or deadlines

    return {
        "facts": {
            "has_deadlines": True,
            "deadlines": filtered[:10],
            "scope": scope
        },
        "targets": []
    }


def exec_requirement_check(page_context: str, user_msg: str) -> Dict[str, Any]:
    restrictions = parse_course_page_restrictions(page_context or "")
    matched = None

    if restrictions:
        q = normalize_text(user_msg)
        best = []
        for item in restrictions:
            title = normalize_text(item.get("title", ""))
            score = 0
            if title and title in q:
                score += 5
            if "лаб" in q and ("лаб" in title or "практическ" in title):
                score += 3
            if "тест" in q and "тест" in title:
                score += 3
            if "лекц" in q and "лекц" in title:
                score += 3
            if item.get("is_restricted"):
                score += 2
            if score > 0:
                best.append((score, item))

        if best:
            best.sort(key=lambda x: x[0], reverse=True)
            matched = best[0][1]
        elif len(restrictions) == 1:
            matched = restrictions[0]

    return {
        "facts": {
            "has_restrictions": bool(restrictions),
            "matched_restriction": matched,
            "restriction_count": len(restrictions)
        },
        "targets": []
    }


def exec_answer_from_context(
    db: Session,
    course_id: str,
    viewer_role: str,
    query: str,
    scope: str,
    user_msg: str
) -> Dict[str, Any]:
    candidates, score_map = retrieve_candidates(
        db=db,
        course_id=course_id,
        viewer_role=viewer_role,
        search_query=query,
        scope=scope,
        user_msg=user_msg
    )
    return {
        "facts": {
            "query": query,
            "candidates": candidates
        },
        "targets": candidates[:1],
        "score_map": score_map
    }


# =========================
# RESPONSE GENERATOR
# =========================

def generate_response(
    user_msg: str,
    history: List[ChatHistoryItem],
    action: str,
    scope: str,
    execution: Dict[str, Any],
    ontology: Dict[str, Any],
    teachers: str,
    grades: str,
    assign_status: str
) -> Dict[str, Any]:
    facts = execution.get("facts", {})
    targets = [t for t in execution.get("targets", []) if t]

    prompt = f"""
Ты помощник по курсу Moodle.
Тебе нужно ответить естественно, в тоне пользователя, но культурно.
Не используй заготовленные канцелярские фразы.
Не выдумывай факты. Используй только данные ниже.
Если данных недостаточно, прямо скажи об этом.
Если есть target, можно мягко сослаться на него по названию.
Не упоминай внутренние слова вроде "роутер", "контекст", "метаданные".

Верни СТРОГО JSON:
{{
  "reply": "..."
}}

Текущий action: {action}
Текущий scope: {scope}

История:
{chr(10).join([f"{h.role}: {h.content}" for h in history[-6:]])}

Запрос пользователя:
{user_msg}

Курс:
{json.dumps(ontology, ensure_ascii=False)}

Преподаватели:
{teachers or ""}

Оценки:
{grades or ""}

Статус заданий:
{assign_status or ""}

Факты для ответа:
{json.dumps(facts, ensure_ascii=False, default=str)}

Targets:
{json.dumps([{"title": t.get("title"), "url": t.get("url"), "kind": t.get("kind")} for t in targets], ensure_ascii=False)}
"""

    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            response_format={"type": "json_object"}
        )
        data = extract_json_from_text(resp.choices[0].message.content)
        reply = safe_strip(data.get("reply", ""))
        if reply:
            return {"reply": reply, "targets": targets}
    except Exception:
        pass

    # Minimal safe fallback, only if LLM generation fails completely.
    if action == "course_overview":
        title = facts.get("course_title", "Курс")
        start = facts.get("start_module")
        reply = f'{title}: можно начать с "{start["title"]}".' if start else title
    elif action == "count_items":
        reply = f'Количество элементов: {facts.get("count", 0)}.'
    elif action == "find_deadline":
        if facts.get("has_deadlines"):
            reply = "\n".join([f'{d.title}: до {d.due_date}' for d in facts.get("deadlines", [])[:5]])
        else:
            reply = "Я не вижу извлечённых дедлайнов в текущих данных."
    elif action == "navigate":
        if targets:
            reply = f'Откройте "{targets[0]["title"]}".'
        else:
            reply = "Не удалось выбрать следующий материал."
    elif action == "check_requirement":
        matched = facts.get("matched_restriction")
        if matched:
            texts = "; ".join([r["text"] for r in matched.get("restrictions", [])])
            reply = f'Для "{matched.get("title", "этого элемента")}" вижу ограничение: {texts}.'
        else:
            reply = "Явного технического ограничения по текущим данным не видно."
    else:
        if targets:
            reply = f'Ближе всего по теме выглядит "{targets[0]["title"]}".'
        else:
            reply = "Не удалось собрать надёжный ответ по текущим данным."

    return {"reply": reply, "targets": targets}


# =========================
# ENDPOINTS
# =========================

@app.post("/api/course/sync")
def sync_course(data: CourseData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()

    if db_course:
        db_course.title = data.title
        db_course.content = data.sections
        db_course.last_updated = datetime.now(timezone.utc)
    else:
        db_course = Course(course_id=data.course_id, title=data.title, content=data.sections)
        db.add(db_course)

    db.commit()
    return {"status": "success"}


@app.post("/api/module/update")
def update_module_content(data: ModuleUpdateData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course:
        return {"status": "error", "reason": "course_not_found"}

    sections = copy.deepcopy(db_course.content)
    mod_title = "Без названия"
    mod_type = data.module_type
    mod_visibility = data.visibility or {}
    updated = False

    for sec in sections:
        for mod in sec.get("modules", []):
            if mod.get("moodle_id") == data.moodle_id:
                mod["content_text"] = "Текст обновлен и разбит на чанки"
                mod["url"] = data.url
                mod["visibility"] = mod_visibility
                mod_title = mod.get("title", "Без названия")
                mod_type = mod.get("type", mod_type)
                updated = True
                break
        if updated:
            break

    db_course.content = sections
    db_course.last_updated = datetime.now(timezone.utc)

    db.query(ModuleIndex).filter(ModuleIndex.moodle_id == data.moodle_id).delete()

    chunks = split_text_into_chunks(data.content_text)
    if not chunks:
        chunks = ["(Нет текстового содержимого)"]

    vectors = embedder.encode([f"{mod_title}\n{chunk}" for chunk in chunks]).tolist()

    for chunk, vector in zip(chunks, vectors):
        db.add(ModuleIndex(
            moodle_id=data.moodle_id,
            course_id=data.course_id,
            module_type=mod_type,
            title=mod_title,
            content_text=chunk,
            url=data.url,
            visibility=mod_visibility,
            embedding=vector
        ))

    db.commit()
    return {"status": "success"}


@app.post("/api/module/bulk-update")
def bulk_update_modules(data: BulkModuleUpdateData, db: Session = Depends(get_db)):
    moodle_ids = [m.moodle_id for m in data.modules]
    if moodle_ids:
        db.query(ModuleIndex).filter(
            ModuleIndex.course_id == data.course_id,
            ModuleIndex.moodle_id.in_(moodle_ids)
        ).delete(synchronize_session=False)

    texts_to_embed, chunk_metadata = [], []

    for incoming_mod in data.modules:
        mod_title = incoming_mod.title if incoming_mod.title else "Без названия"
        mod_type = incoming_mod.module_type
        mod_visibility = incoming_mod.visibility or {}
        chunks = split_text_into_chunks(incoming_mod.content_text)

        if not chunks:
            continue

        for chunk in chunks:
            texts_to_embed.append(f"{mod_title}\n{chunk}")
            chunk_metadata.append({
                "moodle_id": incoming_mod.moodle_id,
                "module_type": mod_type,
                "title": mod_title,
                "content_text": chunk,
                "url": incoming_mod.url,
                "visibility": mod_visibility
            })

    if texts_to_embed:
        for i in range(0, len(texts_to_embed), 16):
            batch_texts = texts_to_embed[i:i + 16]
            vectors = embedder.encode(batch_texts).tolist()

            for j, vector in enumerate(vectors):
                meta = chunk_metadata[i + j]
                db.add(ModuleIndex(
                    moodle_id=meta["moodle_id"],
                    course_id=data.course_id,
                    module_type=meta["module_type"],
                    title=meta["title"],
                    content_text=meta["content_text"],
                    url=meta["url"],
                    visibility=meta["visibility"],
                    embedding=vector
                ))

            db.commit()

    return {"status": "success", "updated_chunks": len(texts_to_embed)}


@app.post("/api/smart-search")
def smart_search(data: SmartSearchRequest, db: Session = Depends(get_db)):
    indexed_count = db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count()
    if indexed_count == 0:
        return {"reply": "Курс еще не проиндексирован.", "targets": []}

    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course:
        return {"reply": "Курс не найден.", "targets": []}

    user_msg = safe_strip(data.message)
    viewer_role = data.viewer_role or "student"

    course_modules = build_course_modules(db_course, viewer_role)
    ontology = build_course_ontology(
        course_modules=course_modules,
        deadlines=data.deadlines,
        course_title=data.course_title or db_course.title
    )

    route = route_request(
        user_msg=user_msg,
        history=data.history,
        ontology=ontology,
        has_deadlines=bool(data.deadlines),
        has_page_context=bool(data.page_context)
    )

    debug_context = [
        {"title": "action", "text": route["action"], "score": 0},
        {"title": "scope", "text": route["scope"], "score": 0},
        {"title": "router_query", "text": route["query"], "score": 0},
        {"title": "router_reason", "text": route["reason"], "score": 0},
    ]

    execution: Dict[str, Any]

    if route["action"] == "navigate":
        execution = exec_navigation(course_modules, data.history)

    elif route["action"] == "course_overview":
        execution = exec_course_overview(ontology)

    elif route["action"] == "count_items":
        execution = exec_count_items(course_modules, route["scope"])

    elif route["action"] == "find_deadline":
        execution = exec_deadlines(data.deadlines, route["scope"], user_msg)

    elif route["action"] == "check_requirement":
        execution = exec_requirement_check(data.page_context or "", user_msg)
        debug_context.append({
            "title": "restriction_count",
            "text": str(execution["facts"].get("restriction_count", 0)),
            "score": 0
        })

    elif route["action"] == "clarify":
        previous_user = extract_last_user_message(data.history[:-1]) if len(data.history) > 1 else ""
        merged_query = previous_user or route["query"] or user_msg
        search_query = build_search_query(user_msg, data.history, merged_query)
        execution = exec_answer_from_context(
            db=db,
            course_id=data.course_id,
            viewer_role=viewer_role,
            query=search_query,
            scope="generic",
            user_msg=user_msg
        )
        debug_context.append({"title": "search_query", "text": search_query, "score": 0})

    elif route["action"] in {"answer_from_context", "smalltalk"}:
        # smalltalk here still can use grounded facts from ontology without canned text
        if route["action"] == "smalltalk":
            execution = {
                "facts": {
                    "course_title": ontology.get("course_title", ""),
                    "start_module": ontology.get("start_module"),
                    "deadlines": data.deadlines[:3]
                },
                "targets": []
            }
        else:
            search_query = build_search_query(user_msg, data.history, route["query"])
            execution = exec_answer_from_context(
                db=db,
                course_id=data.course_id,
                viewer_role=viewer_role,
                query=search_query,
                scope=route["scope"],
                user_msg=user_msg
            )
            debug_context.append({"title": "search_query", "text": search_query, "score": 0})

    else:
        search_query = build_search_query(user_msg, data.history, route["query"])
        execution = exec_answer_from_context(
            db=db,
            course_id=data.course_id,
            viewer_role=viewer_role,
            query=search_query,
            scope=route["scope"],
            user_msg=user_msg
        )
        debug_context.append({"title": "search_query", "text": search_query, "score": 0})

    # Add candidate debug if present
    if execution.get("facts", {}).get("candidates"):
        for idx, c in enumerate(execution["facts"]["candidates"][:5], start=1):
            score_val = execution.get("score_map", {}).get(c["id"], 0)
            debug_context.append({
                "title": f"{idx}. {c['title']}",
                "text": c["content_text"][:180] + "...",
                "score": score_val
            })

    response_data = generate_response(
        user_msg=user_msg,
        history=data.history,
        action=route["action"],
        scope=route["scope"],
        execution=execution,
        ontology=ontology,
        teachers=data.teachers,
        grades=data.grades,
        assign_status=data.assign_status
    )

    reply = response_data["reply"]
    targets = [{
        "id": t["moodle_id"],
        "url": t["url"],
        "title": t["title"],
        "snippet": ""
    } for t in response_data.get("targets", []) if t]

    log_id = None
    try:
        new_log = ChatLog(
            course_id=data.course_id,
            viewer_role=viewer_role,
            user_query=data.message,
            ai_reply=reply,
            used_context=(
                f"action={route['action']}\n"
                f"scope={route['scope']}\n"
                f"router_query={route['query']}\n"
                f"router_reason={route['reason']}\n"
                f"targets={[t['title'] for t in response_data.get('targets', []) if t]}\n"
            )
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)
        log_id = new_log.id
    except Exception:
        pass

    return {
        "reply": reply,
        "targets": targets,
        "debug_context": debug_context,
        "debug_meta": {
            "action": route["action"],
            "scope": route["scope"],
            "router_query": route["query"],
            "router_reason": route["reason"],
            "chosen_title": targets[0]["title"] if targets else "",
            "chosen_kind": response_data.get("targets", [{}])[0].get("kind", "") if response_data.get("targets") else "",
            "last_nav_target": extract_last_bot_navigation_target(data.history)
        },
        "log_id": log_id
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