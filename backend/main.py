import os
import re
import json
import copy
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Depends, Request, Response
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


# --- СХЕМЫ ДАННЫХ ---
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


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def db_module_visible_for_role(mod: ModuleIndex, viewer_role: Optional[str]) -> bool:
    visibility = mod.visibility or {}
    if viewer_role == "teacher": return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False): return False
    return True


def split_text_into_chunks(text: str, min_size: int = 200, max_size: int = 1500) -> List[str]:
    if not text: return []
    text = re.sub(r'Печатать книгу.*?Оглавление', '', text, flags=re.IGNORECASE | re.DOTALL)
    paragraphs = re.split(r'\n+', text.strip())
    chunks = []
    current_chunk = ""
    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 10: continue
        if len(current_chunk) + len(p) + 1 <= max_size:
            current_chunk += ("\n" + p if current_chunk else p)
        else:
            if len(current_chunk) >= min_size: chunks.append(current_chunk.strip())
            current_chunk = p
    if current_chunk: chunks.append(current_chunk.strip())
    return chunks


def extract_json_from_text(text: str) -> dict:
    """Надежно извлекает JSON из ответа нейросети, даже если она добавила маркдаун"""
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except:
        pass
    return {}


# --- API ENDPOINTS ---
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
    vectors_count = db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count()
    return {"status": "success", "needs_initial_sync": vectors_count == 0}


@app.post("/api/module/update")
def update_module_content(data: ModuleUpdateData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course: return {"status": "error", "reason": "course_not_found"}
    sections = copy.deepcopy(db_course.content)
    mod_title, mod_type = "Без названия", data.module_type
    mod_visibility, updated = data.visibility or {}, False
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
        if updated: break
    db_course.content = sections
    db_course.last_updated = datetime.now(timezone.utc)
    db.query(ModuleIndex).filter(ModuleIndex.moodle_id == data.moodle_id).delete()
    chunks = split_text_into_chunks(data.content_text)
    if not chunks: chunks = ["(Нет текстового содержимого)"]
    vectors = embedder.encode([f"{mod_title}\n{chunk}" for chunk in chunks]).tolist()
    for chunk, vector in zip(chunks, vectors):
        db_index = ModuleIndex(
            moodle_id=data.moodle_id, course_id=data.course_id, module_type=mod_type,
            title=mod_title, content_text=chunk, url=data.url, visibility=mod_visibility, embedding=vector
        )
        db.add(db_index)
    db.commit()
    return {"status": "success"}


@app.post("/api/module/bulk-update")
def bulk_update_modules(data: BulkModuleUpdateData, db: Session = Depends(get_db)):
    moodle_ids = [m.moodle_id for m in data.modules]
    if moodle_ids:
        db.query(ModuleIndex).filter(
            ModuleIndex.course_id == data.course_id, ModuleIndex.moodle_id.in_(moodle_ids)
        ).delete(synchronize_session=False)

    texts_to_embed, chunk_metadata = [], []
    for incoming_mod in data.modules:
        mod_title = incoming_mod.title if hasattr(incoming_mod, 'title') and incoming_mod.title else "Без названия"
        mod_type = incoming_mod.module_type
        mod_visibility = incoming_mod.visibility or {}
        chunks = split_text_into_chunks(incoming_mod.content_text)
        if not chunks: continue
        for chunk in chunks:
            texts_to_embed.append(f"{mod_title}\n{chunk}")
            chunk_metadata.append({
                "moodle_id": incoming_mod.moodle_id, "module_type": mod_type,
                "title": mod_title, "content_text": chunk, "url": incoming_mod.url, "visibility": mod_visibility
            })

    if texts_to_embed:
        for i in range(0, len(texts_to_embed), 16):
            batch_texts = texts_to_embed[i:i + 16]
            vectors = embedder.encode(batch_texts).tolist()
            for j, vector in enumerate(vectors):
                meta = chunk_metadata[i + j]
                db_index = ModuleIndex(
                    moodle_id=meta["moodle_id"], course_id=data.course_id, module_type=meta["module_type"],
                    title=meta["title"], content_text=meta["content_text"], url=meta["url"],
                    visibility=meta["visibility"], embedding=vector
                )
                db.add(db_index)
            db.commit()
    return {"status": "success", "updated_chunks": len(texts_to_embed)}


@app.post("/api/smart-search")
def smart_search(data: SmartSearchRequest, db: Session = Depends(get_db)):
    indexed_count = db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count()
    if indexed_count == 0:
        return {"reply": "Курс еще не проиндексирован. Пожалуйста, подождите пару минут.", "targets": []}

    user_msg = data.message.strip()
    viewer_role = data.viewer_role or "student"

    # === ШАГ 1. REFORMULATOR (Умное восстановление контекста) ===
    history_text = "\n".join([f"{h.role}: {h.content}" for h in data.history[-3:]])
    reformulator_prompt = f"""Твоя задача — проанализировать историю диалога и переписать текущий запрос пользователя в четкий поисковый запрос.
Правила:
1. Если запрос - это бессмысленный набор букв (например "--_--"), эмоция, благодарность ("спасибо"), ругань ("ало") или просто приветствие — верни СТРОГО: {{"query": "SMALLTALK"}}
2. Замени сленг: "5", "пятерка" -> "отлично"; "автомат" -> "освобождение от экзамена"; "препод" -> "преподаватель".
3. Если запрос уточняющий ("а еще что?", "где почитать?"), добавь контекст из истории.
4. В остальных случаях выдели главный термин или предмет.

Отвечай СТРОГО в формате JSON, без лишних слов:
{{
    "query": "текст запроса или SMALLTALK"
}}

История диалога:
{history_text}
Текущий запрос: "{user_msg}"
JSON:"""

    try:
        ref_response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": reformulator_prompt}],
            temperature=0.0
        )
        ref_json = extract_json_from_text(ref_response.choices[0].message.content)
        search_query = ref_json.get("query", user_msg).strip().lower()
    except:
        search_query = user_msg.lower()

    # === ШАГ 2. ВЕКТОРНЫЙ ПОИСК ===
    visible_chunks = []
    chunk_scores = {}
    context_str = ""

    if search_query == "smalltalk":
        context_str = "[ВЕКТОРНЫЙ ПОИСК ОТКЛЮЧЕН. Поддержи беседу или попроси задать вопрос по курсу.]"
    else:
        keywords = set(re.sub(r'[^\w\s-]', '', search_query).split())
        query_vector = embedder.encode([search_query])[0].tolist()
        distance_col = ModuleIndex.embedding.cosine_distance(query_vector)
        raw_results = db.query(ModuleIndex, distance_col.label('distance')).filter(
            ModuleIndex.course_id == data.course_id
        ).order_by(distance_col).limit(100).all()

        scored_chunks = []
        for c, dist in raw_results:
            title_lower = (c.title or "").lower()
            text_lower = (c.content_text or "").lower()
            match_bonus = 0

            if search_query in text_lower:
                match_bonus += 0.5
            elif search_query in title_lower:
                match_bonus += 0.4

            for kw in keywords:
                if len(kw) < 4: continue
                root = kw[:-2] if len(kw) > 4 else kw
                if root in title_lower and c.module_type != 'quiz': match_bonus += 0.1
                if root in text_lower: match_bonus += 0.1

            final_score = dist - match_bonus
            if c.module_type == 'quiz': final_score += 0.3
            scored_chunks.append((final_score, c))

        scored_chunks.sort(key=lambda x: x[0])
        chunk_scores = {c.id: score for score, c in scored_chunks}

        visible_chunks = [c for score, c in scored_chunks if db_module_visible_for_role(c, viewer_role)]

        # Берем топ-5 кусков текста
        context_lines = [f"ID: {c.moodle_id} | Название: {c.title}\nТекст: {c.content_text}" for c in
                         visible_chunks[:5]]
        context_str = "\n\n".join(context_lines) if context_lines else "НЕТ ДАННЫХ В ЛЕКЦИЯХ"

    deadline_str = "\n".join(
        [f"- {d.title} (до {d.due_date})" for d in data.deadlines]) if data.deadlines else "НЕТ ДЕДЛАЙНОВ"

    # === ШАГ 3. ФИНАЛЬНЫЙ JSON-АГЕНТ ===
    sys_prompt = f"""Ты — экспертный ИИ-ассистент СДО Moodle. Твоя задача помогать с навигацией и теорией.
Роль пользователя: {viewer_role}.

[МЕТАДАННЫЕ КУРСА]
{data.teachers}
Оглавление (Карта курса с идентификаторами):
{data.course_map}

[ЛИЧНЫЕ ДАННЫЕ СТУДЕНТА]
{data.grades}
{data.assign_status}
Дедлайны: {deadline_str}

[ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ]
{context_str}

ТЫ ОБЯЗАН ОТВЕТИТЬ СТРОГО В ФОРМАТЕ JSON! НИКАКОГО ДОПОЛНИТЕЛЬНОГО ТЕКСТА!
ПРАВИЛА ГЕНЕРАЦИИ ОТВЕТА (reply):
1. Внимательно изучи ВСЕ предоставленные тексты и Оглавление.
2. Если студент спрашивает про критерии оценки (например "как получить 5/отлично", "будет ли автомат"), СНАЧАЛА поищи их в [ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ]. Если нашел — ОБЪЕДИНИ информацию (например: баллы + условия по лабам). Если инфы ТОЧНО нет — скажи "Обратитесь к преподавателю".
3. Навигация ("С чего начать?"): выбери первый актуальный материал из Оглавления, который не скрыт.
4. Отвечай вежливо, кратко (1-4 предложения) и по существу.

ФОРМАТ ОТВЕТА:
{{
    "thought": "Здесь напиши свои рассуждения. Что спросили? Есть ли это в тексте?",
    "reply": "Здесь готовый текст ответа студенту.",
    "navigate_ids": ["module-12345"]
}}
В поле navigate_ids укажи от 0 до 3 идентификаторов (ID), которые ты рекомендуешь студенту. ID бери ТОЛЬКО из Оглавления или Фрагментов (начинаются на module-...).
"""

    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]

    try:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=800  # Увеличили, чтобы влез JSON
        )
        agent_json = extract_json_from_text(response.choices[0].message.content)

        reply = agent_json.get("reply", "Извините, не смог структурировать ответ.")
        nav_ids = agent_json.get("navigate_ids", [])

        # Формируем красивый дебаг для мыслей ИИ
        thought = agent_json.get("thought", "Нет рассуждений.")

    except Exception as e:
        return {"reply": "Произошла ошибка генерации ответа.", "targets": []}

    # === ШАГ 4. СБОРК КНОПОК ===
    unique_targets = []
    seen_ids = set()

    for tid in nav_ids:
        if not isinstance(tid, str): continue
        target_id = tid.strip()
        if target_id in seen_ids: continue

        target_mod = db.query(ModuleIndex).filter(
            ModuleIndex.moodle_id == target_id,
            ModuleIndex.course_id == data.course_id
        ).first()

        if target_mod and db_module_visible_for_role(target_mod, viewer_role):
            unique_targets.append({
                "id": target_mod.moodle_id,
                "url": target_mod.url,
                "title": target_mod.title,
                "snippet": ""
            })
            seen_ids.add(target_id)

    # === ШАГ 5. ЛОГИРОВАНИЕ И ДЕБАГ ===
    debug_context = []
    debug_context.append({"title": "🧠 Рассуждения ИИ (Chain of Thought)", "text": thought, "score": 0})

    if search_query != "smalltalk":
        debug_context.append({"title": f"🔍 Распознанный запрос", "text": search_query, "score": 0})
        for c in visible_chunks[:4]:
            debug_context.append({
                "title": c.title,
                "text": c.content_text[:150] + "...",
                "score": round(chunk_scores.get(c.id, 0), 4)
            })

    log_id = None
    try:
        new_log = ChatLog(
            course_id=data.course_id,
            viewer_role=viewer_role,
            user_query=data.message,
            ai_reply=reply,
            used_context=f"Query: {search_query}\nThoughts: {thought}\n\n" + context_str[:1500]
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)
        log_id = new_log.id
    except Exception as e:
        print(f"Ошибка лога: {e}")

    return {
        "reply": reply,
        "targets": unique_targets,
        "debug_context": debug_context,
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