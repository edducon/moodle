import os
import re
import copy
import urllib.parse
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
    """Определяет формат материала, чтобы ИИ понимал, куда отправлять пользователя."""
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


def split_text_into_chunks(text: str, min_size: int = 200, max_size: int = 900) -> List[str]:
    """Умная нарезка текста по абзацам (Semantic Chunking)."""
    if not text:
        return []

    # Скрипт Moodle (content.js) присылает нам абзацы, разделенные переносами строк \n
    paragraphs = re.split(r'\n+', text.strip())

    chunks = []
    current_chunk = ""

    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 10:  # Игнорируем пустые строки и мусорные огрызки
            continue

        # Если сам по себе абзац просто гигантский (например, препод забыл нажать Enter)
        if len(p) > max_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            # Режем гиганта кусками (очень редкий случай)
            start = 0
            while start < len(p):
                chunks.append(p[start:start + max_size])
                start += max_size
            continue

        # Пытаемся добавить абзац к текущему чанку
        if len(current_chunk) + len(p) + 1 <= max_size:
            current_chunk += ("\n" + p if current_chunk else p)
        else:
            # Чанк заполнен! Сохраняем его и начинаем новый
            if len(current_chunk) >= min_size:
                chunks.append(current_chunk.strip())
            current_chunk = p

    # Сохраняем последний кусочек, если он остался
    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def get_best_snippet(chunk_text: str, query: str) -> str:
    """Ищет предложение с ключевым словом."""
    if not chunk_text or not query:
        return ""

    sentences = re.split(r'(?<=[.!?])\s+', chunk_text)
    # Сюда уже будет прилетать расширенный запрос с синонимами
    query_words = set(re.findall(r'[а-яА-Яa-zA-Z0-9]{4,}', query.lower()))
    stop_words = {"что", "такое", "какой", "какие", "где", "когда", "почему", "зачем", "как", "расскажи", "виды"}
    keywords = {w for w in query_words if w not in stop_words}

    best_sentence = sentences[0] if sentences else chunk_text
    max_score = -1

    for s in sentences:
        s_lower = s.lower()
        score = 0
        for kw in keywords:
            root = kw[:-2] if len(kw) > 5 else kw
            if root in s_lower:
                score += 1
                if re.search(rf'\b{root}[а-яa-z]*\s*[-–—]', s_lower): score += 5
                if re.search(rf'\b{root}[а-яa-z]*\s+(это|позволяет|является|представляет|означает)', s_lower): score += 4
                if s_lower.strip().startswith(root): score += 2

        if score > max_score:
            max_score = score
            best_sentence = s

    words = best_sentence.split()
    target_idx = -1
    for i, w in enumerate(words):
        w_lower = w.lower()
        if any(kw[:-2] in w_lower for kw in keywords if len(kw) > 3):
            target_idx = i
            break

    # Умный Фолбэк: если слово так и не нашли (например, из-за опечатки "отстутпы"),
    # но нейросеть уверена, что текст правильный — возвращаем начало абзаца!
    if target_idx == -1:
        fallback_snippet = " ".join(sentences[0].split()[:6]) if sentences else " ".join(words[:6])
        return re.sub(r'\s+', ' ', fallback_snippet).strip()

    start = max(0, target_idx - 2)
    end = min(len(words), target_idx + 5)
    snippet = " ".join(words[start:end])
    return re.sub(r'\s+', ' ', snippet).strip()

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

    # === 1. ГЛОБАЛЬНОЕ РАСШИРЕНИЕ ЗАПРОСА (СИНОНИМЫ) ===
    query_expanded = user_msg.lower()
    synonyms = {
        "лаборатор": "отчет практическ",
        "лаб": "отчет",
        "ворд": "word",
        "пдф": "pdf",
        "отступ": "форматирование поля интервал шрифт"  # Докинули термины для верности
    }
    for k, v in synonyms.items():
        if k in query_expanded:
            query_expanded += f" {v}"

    query_vector = embedder.encode([user_msg])[0].tolist()

    # === 2. ГИБРИДНЫЙ ПОИСК И СКОРРИНГ ===
    distance_col = ModuleIndex.embedding.cosine_distance(query_vector)
    raw_results = db.query(ModuleIndex, distance_col.label('distance')).filter(
        ModuleIndex.course_id == data.course_id
    ).order_by(distance_col).limit(100).all()

    stop_words = {"что", "такое", "какой", "какие", "где", "когда", "почему", "зачем", "как", "расскажи", "виды"}
    # Сортировщик теперь использует расширенный запрос!
    query_words = set(re.findall(r'[а-яА-Яa-zA-Z0-9]{4,}', query_expanded))
    keywords = {w for w in query_words if w not in stop_words}

    scored_chunks = []
    for c, dist in raw_results:
        text_lower = (c.content_text or "").lower()
        title_lower = (c.title or "").lower()

        match_bonus = 0
        for kw in keywords:
            root = kw[:-2] if len(kw) > 5 else kw
            # МЕГА-БОНУС: если слово (или синоним) есть в заголовке, лекция взлетает в топ!
            if root in title_lower:
                match_bonus += 0.4
            elif root in text_lower:
                match_bonus += 0.15

        final_score = dist - match_bonus
        scored_chunks.append((final_score, c))

    scored_chunks.sort(key=lambda x: x[0])
    raw_chunks = [c for score, c in scored_chunks]

    # Берем ТОП-5 релевантных материалов (даем нейросети больше контекста)
    context_lines = []
    for c in visible_chunks[:5]:
        mod_format = get_module_format(c.title, c.module_type, c.url)
        context_lines.append(
            f"--- НАЧАЛО МАТЕРИАЛА [ID: {c.moodle_id}] ---\nНазвание: {c.title}\nФормат: {mod_format}\nТекст:\n{c.content_text}\n--- КОНЕЦ МАТЕРИАЛА ---")

    context_str = "\n\n".join(context_lines) if context_lines else "ПУСТО. В курсе нет материалов по этому запросу."

    deadline_lines = [f"- {d.title} (до {d.due_date})" for d in data.deadlines]
    deadline_str = "\n".join(deadline_lines) if deadline_lines else "НЕТ_ДЕДЛАЙНОВ"

    sys_prompt = f"""Ты — СТРОГИЙ БОТ-НАВИГАТОР по образовательному курсу. Твоя ЕДИНСТВЕННАЯ задача — подсказать пользователю, ГДЕ находится информация.

АБСОЛЮТНЫЕ ПРАВИЛА (НАРУШАТЬ ЗАПРЕЩЕНО):
1. ЗАПРЕТ НА ОБЪЯСНЕНИЯ И ОПРЕДЕЛЕНИЯ: Если пользователь спрашивает "Что такое X?" или просит объяснить суть, НИКОГДА не пиши само определение! Твой ответ должен быть: "Информацию об этом можно найти в материале [Название материала]". 
2. ЧИСТОТА ОТВЕТА: Никогда не используй технические идентификаторы (например, module-12345) в тексте сообщения. Только человеческие названия лекций.
3. ФИЛЬТРАЦИЯ МУСОРА: Игнорируй фрагменты, содержащие технический код, JSON, служебные сообщения (например, "fileexistsdialog", "stacktrace", "renderer"). Если фрагмент — это системная ошибка или код, делай вид, что его не существует.
4. ПРИОРИТЕТ ТЕКСТА (ТЕГИ): Тег [NAVIGATE: ID_материала] ставь ТОЛЬКО для 'Текстовый материал' или 'Папка с файлами'. Для 'Видеолекция' или 'Форум' просто напиши текстом: "Также есть видеолекция по этой теме".
5. ЛОГИКА ПОИСКА: Если тема упоминается в 'Фрагментах лекций', ты обязан дать навигационную ссылку. Не говори "не нашел", если слово есть в контексте.
6. ЗЕРКАЛЬНЫЙ СТИЛЬ: Отвечай в стиле пользователя (официально, дружелюбно или кратко), но не выходи за рамки роли навигатора.
7. ЕДИНСТВЕННЫЙ ТЕГ: В конце сообщения добавь ровно ОДИН тег [NAVIGATE: ID_материала] для самого релевантного источника, даже если информация размазана по нескольким.

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
            temperature=0.3,
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
                # ИСПРАВЛЕНИЕ 1: Передаем query_expanded, чтобы синонимы работали и здесь!
                target_snippet = get_best_snippet(c.content_text, query_expanded)
                break

    # ИСПРАВЛЕНИЕ 2: Сдвинули блок влево (он больше не внутри if nav_match)
    # === ГЕНЕРАЦИЯ КНОПОК ===
    unique_targets = []
    seen_titles = set()

    for c in visible_chunks[:5]:
        clean_title = (c.title or "").strip().lower()
        if clean_title not in seen_titles:
            seen_titles.add(clean_title)
            snippet = get_best_snippet(c.content_text, query_expanded)

            unique_targets.append({
                "id": c.moodle_id,
                "url": c.url,
                "title": c.title,
                "snippet": snippet
            })

        if len(unique_targets) >= 4:
            break

    debug_context = []
    for c in visible_chunks[:3]:
        debug_context.append({"title": c.title, "text": c.content_text})

    return {
        "reply": reply,
        "target_url": target_url,
        "target_id": target_id,
        "target_snippet": target_snippet,
        "targets": unique_targets,
        "debug_context": debug_context
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)