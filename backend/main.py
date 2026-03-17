import os
import re
import copy
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from config import settings
from database import SessionLocal, Course, ModuleIndex

app = FastAPI(title="Moodle Assistant API")

client = OpenAI(base_url=settings.OLLAMA_URL, api_key="ollama")
embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

from fastapi.middleware.cors import CORSMiddleware

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


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def db_module_visible_for_role(mod: ModuleIndex, viewer_role: Optional[str]) -> bool:
    visibility = mod.visibility or {}
    if viewer_role == "teacher":
        return True
    if visibility.get("is_hidden", False) or visibility.get("has_restrictions", False):
        return False
    return True


def get_module_format(title: str, mod_type: str, url: str) -> str:
    title_lower = (title or "").lower()

    if mod_type == "quiz" or "тест" in title_lower:
        return "Тест / Экзамен"

    if mod_type == "assign" or "задание" in title_lower or "практическ" in title_lower:
        return "Практическое задание"

    if "видео" in title_lower or "youtube" in str(url):
        return "Видеолекция"

    if mod_type in ["forum", "chat"] or "перекличка" in title_lower or "обсужден" in title_lower:
        return "Форум / Обсуждение"

    if mod_type == "folder":
        return "Папка с файлами"

    if mod_type == "checklist":
        return "Контрольный список (Чек-лист)"

    return "Текстовый материал"


def split_text_into_chunks(text: str, min_size: int = 200, max_size: int = 1500) -> List[str]:
    """Умное семантическое разделение текста с защитой от разрыва слов и очисткой мусора Moodle."""
    if not text:
        return []

    text = re.sub(r'Печатать книгу.*?Оглавление', '', text, flags=re.IGNORECASE | re.DOTALL)

    paragraphs = re.split(r'\n+', text.strip())
    chunks = []
    current_chunk = ""

    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 10:
            continue

        if len(p) > max_size:
            sentences = re.split(r'(?<=[.!?])\s+', p)
            for s in sentences:
                if len(s) > max_size:
                    words = s.split()
                    for w in words:
                        if len(current_chunk) + len(w) + 1 <= max_size:
                            current_chunk += (" " + w if current_chunk else w)
                        else:
                            if len(current_chunk) >= min_size:
                                chunks.append(current_chunk.strip())
                            current_chunk = w
                else:
                    if len(current_chunk) + len(s) + 1 <= max_size:
                        current_chunk += (" " + s if current_chunk else s)
                    else:
                        if len(current_chunk) >= min_size:
                            chunks.append(current_chunk.strip())
                        current_chunk = s
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
        db_index = ModuleIndex(
            moodle_id=data.moodle_id,
            course_id=data.course_id,
            module_type=mod_type,
            title=mod_title,
            content_text=chunk,
            url=data.url,
            visibility=mod_visibility,
            embedding=vector
        )
        db.add(db_index)

    db.commit()
    return {"status": "success"}


@app.post("/api/module/bulk-update")
def bulk_update_modules(data: BulkModuleUpdateData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course:
        return {"status": "error", "reason": "course_not_found"}

    moodle_ids_to_update = [m.moodle_id for m in data.modules]
    if moodle_ids_to_update:
        db.query(ModuleIndex).filter(
            ModuleIndex.course_id == data.course_id,
            ModuleIndex.moodle_id.in_(moodle_ids_to_update)
        ).delete(synchronize_session=False)

    texts_to_embed = []
    chunk_metadata = []

    for incoming_mod in data.modules:
        mod_title = incoming_mod.title if hasattr(incoming_mod, 'title') and incoming_mod.title else "Без названия"
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
        batch_size = 16
        for i in range(0, len(texts_to_embed), batch_size):
            batch_texts = texts_to_embed[i:i + batch_size]
            vectors = embedder.encode(batch_texts).tolist()

            for j, vector in enumerate(vectors):
                meta = chunk_metadata[i + j]
                db_index = ModuleIndex(
                    moodle_id=meta["moodle_id"],
                    course_id=data.course_id,
                    module_type=meta["module_type"],
                    title=meta["title"],
                    content_text=meta["content_text"],
                    url=meta["url"],
                    visibility=meta["visibility"],
                    embedding=vector
                )
                db.add(db_index)

            db.commit()

    return {"status": "success", "updated_chunks": len(texts_to_embed)}


@app.post("/api/smart-search")
def smart_search(data: SmartSearchRequest, db: Session = Depends(get_db)):
    indexed_count = db.query(ModuleIndex).filter(ModuleIndex.course_id == data.course_id).count()
    if indexed_count == 0:
        return {
            "reply": "Курс еще не проиндексирован. Пожалуйста, дайте мне пару минут на изучение материалов, и попробуйте задать вопрос снова.",
            "targets": []
        }

    user_msg = data.message.strip()
    viewer_role = data.viewer_role or "student"

    # === 1. ИЗВЛЕЧЕНИЕ СУТИ ЗАПРОСА (УМНЫЙ ЭКСТРАКТОР ЧЕРЕЗ LLM) ===
    extract_prompt = f"""Внимательно прочитай запрос пользователя. Твоя цель - вытащить только СУТЬ поиска.
Удали любые приветствия, странные обращения ('йоу', 'собака', 'зайка', 'наруто узумаки'), шелуху и глаголы ('где почитать', 'найди', 'подскажи').
Верни ТОЛЬКО термин, тему или предмет поиска. 
Если в тексте только болтовня, приветствие и НЕТ конкретного предмета для поиска (например "как дела", "привет", "с чего начать курс"), верни ровно одно слово: NONE.
Отвечай строго без кавычек и точек.

Запрос: "{user_msg}"
Суть:"""

    try:
        ext_response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.0,
            max_tokens=30
        )
        search_query = ext_response.choices[0].message.content.strip().lower()
        search_query = re.sub(r'[^\w\s-]', '', search_query)  # Убираем лишнюю пунктуацию
    except Exception as e:
        search_query = "none"

    if search_query == "none" or not search_query:
        # Это smalltalk или абстрактный вопрос. Ищем по всему вектору, но без бонусов.
        search_query_vector_text = user_msg.lower()
        keywords = set()
    else:
        # Идеальный чистый запрос для поиска!
        search_query_vector_text = search_query
        keywords = set(search_query.split())

    query_vector = embedder.encode([search_query_vector_text])[0].tolist()

    # === 2. ВЕКТОРНЫЙ ПОИСК И СКОРИНГ ===
    distance_col = ModuleIndex.embedding.cosine_distance(query_vector)
    raw_results = db.query(ModuleIndex, distance_col.label('distance')).filter(
        ModuleIndex.course_id == data.course_id
    ).order_by(distance_col).limit(100).all()

    scored_chunks = []
    for c, dist in raw_results:
        title_lower = (c.title or "").lower()
        text_lower = (c.content_text or "").lower()
        match_bonus = 0

        # Мощный бонус за точное совпадение ЧИСТОЙ фразы в тексте
        if search_query != "none" and search_query in text_lower:
            match_bonus += 0.5
        elif search_query != "none" and search_query in title_lower:
            match_bonus += 0.4

        matches = 0
        for kw in keywords:
            if len(kw) < 4: continue  # Игнорируем предлоги
            root = kw[:-2] if len(kw) > 4 else kw
            if root in title_lower and c.module_type != 'quiz':
                match_bonus += 0.1
            if root in text_lower:
                matches += 1
                match_bonus += 0.1

        final_score = dist - match_bonus

        if c.module_type == 'quiz':
            final_score += 0.3

        scored_chunks.append((final_score, c))

    scored_chunks.sort(key=lambda x: x[0])
    raw_chunks = [c for score, c in scored_chunks]
    chunk_scores = {c.id: score for score, c in scored_chunks}

    visible_chunks = [c for c in raw_chunks if db_module_visible_for_role(c, viewer_role)]

    context_lines = []
    for c in visible_chunks[:5]:
        mod_format = get_module_format(c.title, c.module_type, c.url)
        context_lines.append(
            f"--- МАТЕРИАЛ ---\nID: {c.moodle_id}\nНазвание: {c.title}\nФормат: {mod_format}\nТекст:\n{c.content_text}\n----------------")

    context_str = "\n\n".join(context_lines) if context_lines else "ПУСТО. В курсе нет материалов по этому запросу."

    deadline_lines = [f"- {d.title} (до {d.due_date})" for d in data.deadlines]
    deadline_str = "\n".join(deadline_lines) if deadline_lines else "НЕТ_ДЕДЛАЙНОВ"

    # === 3. ЖЕЛЕЗОБЕТОННЫЙ ПРОМПТ С FEW-SHOT ПРИМЕРОМ ===
    sys_prompt = f"""Ты — СТРОГИЙ БОТ-НАВИГАТОР по образовательному курсу. Твоя задача — подсказать пользователю, ГДЕ находится информация.

АБСОЛЮТНЫЕ ПРАВИЛА:
1. ИСПОЛЬЗУЙ НАЗВАНИЯ: Бери название материала ТОЛЬКО из поля "Название" во фрагментах. Не придумывай свои названия.
2. НИКАКИХ ID В ТЕКСТЕ: Никогда не пиши технические идентификаторы (типа module-12345) в тексте самого сообщения для пользователя.
3. НАВИГАЦИЯ: Если ты рекомендуешь пользователю открыть какой-либо материал (даже на общий вопрос вроде "с чего начать"), ты ОБЯЗАН добавить в самый конец ответа с новой строки тег: [NAVIGATE: ID_материала]. 
4. СНИППЕТ: Если пользователь ищет конкретное понятие или термин, найди точную цитату (1-3 предложения) в тексте и оберни ее в тег [SNIPPET: точная цитата]. Если вопрос общий (типа "с чего начать"), оставь тег пустым: [SNIPPET: ].
5. СТРОГО ОДНА КНОПКА: Выдавай только ОДИН тег [NAVIGATE] для самого подходящего материала. Нельзя выдавать несколько тегов!
6. SMALLTALK: Если пользователь просто здоровается (smalltalk) и ничего не ищет, ответь в его стиле. Теги NAVIGATE и SNIPPET не ставь.

=== ПРИМЕР ИДЕАЛЬНОГО ОТВЕТА НА ПОИСК ТЕРМИНА ===
Привет, мой господин! Информацию о нагрузочном тестировании можно найти в материале "4.1 Лекция №3".
[NAVIGATE: module-234547]
[SNIPPET: Нагрузочное тестирование (Performance and Load Testing) – вид тестирования производительности, проводимый с целью оценки поведения компонента...]
================================

=== ПРИМЕР ИДЕАЛЬНОГО ОТВЕТА НА ОБЩИЙ ВОПРОС ===
Конечно, мой господин! Рекомендую начать изучение с материала "1.1 Вводная лекция".
[NAVIGATE: module-123456]
[SNIPPET: ]
================================

Фрагменты лекций:
{context_str}

Дедлайны:
{deadline_str}
"""

    messages = [{"role": "system", "content": sys_prompt}]
    for h in data.history[-4:]:
        messages.append({"role": h.role if h.role in ["user", "assistant"] else "user", "content": h.content})
    messages.append({"role": "user", "content": user_msg})

    try:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=500
        )
        reply = response.choices[0].message.content
    except Exception as e:
        print(f"🚨 ОШИБКА OLLAMA: {e}")
        return {"reply": "Произошла ошибка при обращении к нейросети.", "targets": []}

    target_id = None
    target_url = None
    target_snippet = None

    nav_match = re.search(r'\[NAVIGATE:\s*(.*?)\]', reply)
    if nav_match:
        target_id = nav_match.group(1).strip()
        reply = re.sub(r'\[NAVIGATE:\s*.*?\]', '', reply).strip()

        for c in visible_chunks:
            if c.moodle_id == target_id:
                target_url = c.url
                break

    snippet_match = re.search(r'\[SNIPPET:\s*(.*?)\]', reply, re.DOTALL)
    if snippet_match:
        target_snippet = snippet_match.group(1).strip()
        reply = re.sub(r'\[SNIPPET:\s*.*?\]', '', reply, flags=re.DOTALL).strip()

    # === ЗАЩИТА ОТ ГАЛЛЮЦИНАЦИЙ ИИ (КРОСС-ЧЕК) ===
    if target_snippet:
        snippet_clean = re.sub(r'[^\w\s]', '', target_snippet[:60].lower())
        if snippet_clean:
            for c in visible_chunks:
                chunk_clean = re.sub(r'[^\w\s]', '', c.content_text.lower())
                if snippet_clean in chunk_clean:
                    target_id = c.moodle_id
                    target_url = c.url
                    break

    unique_targets = []
    if target_id:
        for c in visible_chunks:
            if c.moodle_id == target_id:
                unique_targets.append({
                    "id": c.moodle_id,
                    "url": c.url,
                    "title": c.title,
                    "snippet": target_snippet if target_snippet else ""
                })
                break
    elif search_query != "none":
        seen_titles = set()
        for c in visible_chunks[:3]:
            clean_title = (c.title or "").strip().lower()
            if clean_title not in seen_titles:
                seen_titles.add(clean_title)
                unique_targets.append({
                    "id": c.moodle_id,
                    "url": c.url,
                    "title": c.title,
                    "snippet": ""
                })

    debug_context = []
    for c in visible_chunks[:5]:
        debug_context.append({
            "title": c.title,
            "text": c.content_text[:200] + "...",
            "score": round(chunk_scores.get(c.id, 0), 4)
        })

    return {
        "reply": reply,
        "target_url": target_url,
        "target_id": target_id,
        "target_snippet": target_snippet,
        "targets": unique_targets,
        "expanded_query": search_query,
        "debug_context": debug_context
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)