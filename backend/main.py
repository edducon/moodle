import os
import copy
from fastapi import FastAPI, Depends, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import datetime, timezone

from openai import OpenAI
from sentence_transformers import SentenceTransformer

# Импортируем наши модели из database.py
from database import SessionLocal, Course, ModuleIndex

app = FastAPI(title="Moodle Assistant API")

# --- ПОДКЛЮЧЕНИЕ К ЛОКАЛЬНОМУ ИИ (Ollama) ---
# Динамически получаем URL. Если переменной нет (запуск без Докера), используем localhost.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")

client = OpenAI(
    base_url=OLLAMA_URL,
    api_key='ollama'  # Заглушка
)

# Загружаем векторную модель для русского языка (скачается при первом запуске)
embedder = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')


@app.middleware("http")
async def custom_cors_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=200)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- МОДЕЛИ ДАННЫХ ---
class CourseData(BaseModel):
    course_id: str
    title: str
    sections: List[Dict[str, Any]]


class ModuleUpdateData(BaseModel):
    course_id: str
    moodle_id: str
    content_text: str
    url: str


class SmartSearchRequest(BaseModel):
    course_id: str
    message: str


@app.get("/")
def read_root():
    return {"message": "Сервер Moodle Bot работает в штатном режиме!"}


# --- ЭНДПОИНТ 1: СОХРАНЕНИЕ ОГЛАВЛЕНИЯ ---
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


# --- ЭНДПОИНТ 2: СОХРАНЕНИЕ ТЕКСТОВ И ВЕКТОРОВ ЛЕКЦИЙ ---
@app.post("/api/module/update")
def update_module_content(data: ModuleUpdateData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()
    if not db_course:
        return {"status": "error", "message": "Курс не найден"}

    sections = copy.deepcopy(db_course.content)
    mod_title = "Без названия"
    updated = False

    for sec in sections:
        for mod in sec.get("modules", []):
            if mod.get("moodle_id") == data.moodle_id:
                mod["content_text"] = data.content_text
                mod["url"] = data.url
                mod_title = mod.get("title", "Без названия")
                updated = True
                break
        if updated: break

    if updated:
        # Обновляем JSONB для иерархии курса
        db_course.content = sections
        db_course.last_updated = datetime.now(timezone.utc)

        # МАГИЯ ВЕКТОРОВ: Превращаем текст в массив чисел
        text_to_embed = mod_title + " " + data.content_text
        vector = embedder.encode([text_to_embed])[0].tolist()

        # Сохраняем или обновляем плоскую таблицу (для быстрого поиска)
        db_index = db.query(ModuleIndex).filter(ModuleIndex.moodle_id == data.moodle_id).first()
        if db_index:
            db_index.title = mod_title
            db_index.content_text = data.content_text
            db_index.embedding = vector
        else:
            db_index = ModuleIndex(
                moodle_id=data.moodle_id,
                course_id=data.course_id,
                title=mod_title,
                content_text=data.content_text,
                embedding=vector
            )
            db.add(db_index)

        db.commit()
        print(f"✅ Вектор сохранен: {data.moodle_id}")
        return {"status": "success"}

    return {"status": "ignored"}


# --- ЭНДПОИНТ 3: ИИ-НАСТАВНИК (ВЕКТОРНЫЙ ПОИСК В БД + OLLAMA) ---
@app.post("/api/smart-search")
def smart_search(data: SmartSearchRequest, db: Session = Depends(get_db)):
    user_message_lower = data.message.lower()

    # Базовая логика болталки
    if any(w in user_message_lower for w in ["привет", "здравствуй", "хай", "добрый день"]):
        return {
            "reply": "Здравствуйте! Я ваш цифровой наставник 🎓. Какой материал курса нужно помочь найти?",
            "target_id": None}

    # Защита от взлома промпта (Prompt Injection)
    stop_words = ["забудь", "игнорируй", "предыдущие инструкции", "напиши код", "реши за меня"]
    if any(word in user_message_lower for word in stop_words):
        return {"reply": "Хорошая попытка! 😉 Но я здесь, чтобы направлять. Вернемся к навигации?",
                "target_id": None}

    # === НОВОЕ: ДОСТАЕМ ОГЛАВЛЕНИЕ КУРСА ИЗ БАЗЫ ===
    course = db.query(Course).filter(Course.course_id == data.course_id).first()
    course_structure = "Оглавление курса:\n"
    if course and course.content:
        for sec in course.content:
            course_structure += f"- {sec.get('title', 'Раздел')}:\n"
            for mod in sec.get("modules", []):
                # Переводим типы Moodle на понятный русский для ИИ
                m_type = mod.get("type", "")
                if m_type == "quiz":
                    type_ru = "Тест"
                elif m_type == "assign":
                    type_ru = "Задание"
                elif m_type == "page":
                    type_ru = "Лекция"
                else:
                    type_ru = "Элемент"

                course_structure += f"  * [{type_ru}] {mod.get('title', 'Без названия')}\n"

    # 1. Превращаем запрос студента в вектор
    query_vector = embedder.encode([data.message])[0].tolist()

    # 2. Ищем самый близкий по смыслу текст (если он есть)
    closest_module = db.query(ModuleIndex) \
        .filter(ModuleIndex.course_id == data.course_id) \
        .order_by(ModuleIndex.embedding.cosine_distance(query_vector)) \
        .first()

    context_text = ""
    target_id = None
    mod_title = ""

    if closest_module:
        context_text = closest_module.content_text[:1500] if closest_module.content_text else ""
        target_id = closest_module.moodle_id
        mod_title = closest_module.title

    # 3. Формируем промпт для Llama 3.1
    system_prompt = """
    Ты — цифровой ассистент-навигатор по платформе Moodle.
    Твоя цель — помогать студенту находить нужные материалы курса (задания, тесты, лекции), опираясь ТОЛЬКО на предоставленную структуру курса и тексты.

    ПРАВИЛА:
    1. Ты НЕ объясняешь теорию и НЕ решаешь задачи. Твоя роль — дать направление.
    2. Если студент спрашивает про тесты, задания или лекции, найди их в "Оглавлении курса" и перечисли названия.
    3. Отвечай кратко, приветливо (максимум 2-4 предложения) и СТРОГО на русском языке.
    """

    user_prompt = f"""
    {course_structure}

    Найденный отрывок текста по запросу (если есть):
    Название: {mod_title}
    Текст: {context_text}

    Вопрос студента: \"\"\"{data.message}\"\"\"
    Ответь студенту, опираясь на эти данные.
    """

    # 4. Отправляем в Ollama
    try:
        response = client.chat.completions.create(
            model="llama3.1",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.3
        )
        ai_reply = response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка при подключении к Ollama: {e}")
        ai_reply = "Извините, нейромодуль (Ollama) сейчас недоступен. Проверьте, запущена ли она."

    return {"reply": ai_reply, "target_id": target_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)