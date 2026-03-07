import copy

from fastapi import FastAPI, Depends, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import datetime, timezone

# Импортируем наши настройки и модели базы данных
from database import SessionLocal, Course
from config import settings

app = FastAPI(title="Moodle Assistant API")


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


# Зависимость для получения сессии БД
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Pydantic-модель (описывает, какой JSON мы ждем от браузера)
class CourseData(BaseModel):
    course_id: str
    title: str
    sections: List[Dict[str, Any]]


# Базовый эндпоинт, чтобы не было ошибки 404 в корне
@app.get("/")
def read_root():
    return {"message": "Сервер Moodle Bot работает отлично! Перейдите на http://127.0.0.1:8000/docs для просмотра API."}


# ЭНДПОИНТ 1: Сохранение курса (Синхронизация)
@app.post("/api/course/sync")
def sync_course(data: CourseData, db: Session = Depends(get_db)):
    # Ищем, есть ли уже такой курс в БД
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()

    if db_course:
        # Если есть - обновляем контент
        db_course.title = data.title
        db_course.content = data.sections
        db_course.last_updated = datetime.now(timezone.utc)
    else:
        # Если нет - создаем новый
        db_course = Course(
            course_id=data.course_id,
            title=data.title,
            content=data.sections
        )
        db.add(db_course)

    db.commit()
    return {"status": "success", "message": f"Курс '{data.title}' сохранен/обновлен в БД!"}


class ModuleUpdateData(BaseModel):
    course_id: str
    moodle_id: str
    content_text: str
    url: str


# 2. Эндпоинт для сохранения текста лекции внутрь структуры курса
@app.post("/api/module/update")
def update_module_content(data: ModuleUpdateData, db: Session = Depends(get_db)):
    # Ищем курс в БД
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()

    if not db_course:
        return {"status": "error", "message": "Курс не найден в БД. Сначала обновите оглавление."}

    # В SQLAlchemy для обновления JSONB нужно сделать копию, изменить ее и сохранить обратно
    sections = copy.deepcopy(db_course.content)
    updated = False

    # Ищем нужный модуль по всем секциям
    for sec in sections:
        for mod in sec.get("modules", []):
            if mod.get("moodle_id") == data.moodle_id:
                # Нашли! Записываем в него текст и ссылку
                mod["content_text"] = data.content_text
                mod["url"] = data.url
                updated = True
                break
        if updated:
            break

    if updated:
        db_course.content = sections
        db_course.last_updated = datetime.now(timezone.utc)
        db.commit()
        print(f"✅ В БД обновлен контент для модуля: {data.moodle_id}")
        return {"status": "success"}

    return {"status": "ignored", "message": "Модуль не найден в оглавлении"}


# ЭНДПОИНТ 3: Чат с ботом
@app.post("/api/chat")
def chat_with_bot(course_id: str, message: str, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == course_id).first()

    if not db_course:
        return {"reply": "Курс еще не проиндексирован. Пожалуйста, откройте главную страницу курса.", "action": "none"}

    # Вытаскиваем названия всех тем (секции)
    sections = db_course.content
    topic_titles = [sec.get("title", "Без названия") for sec in sections]

    # Формируем красивый список с HTML-переносами строк
    topics_list_html = "<br>".join([f"• {t}" for t in topic_titles])

    return {
        "reply": f"<b>Бот на связи!</b> Вы спросили: <i>'{message}'</i>.<br><br>Вот темы, которые я успешно сохранил в базу:<br>{topics_list_html}",
        "action": "none",
        "target_id": None
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)