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


# ЭНДПОИНТ 2: Чат с ботом
@app.post("/api/chat")
def chat_with_bot(course_id: str, message: str, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == course_id).first()

    if not db_course:
        return {"reply": "Курс еще не проиндексирован. Пожалуйста, откройте главную страницу курса.", "action": "none"}

    return {
        "reply": f"Бот на связи! Вы спросили: '{message}'. В курсе {db_course.title} я насчитал {len(db_course.content)} тем.",
        "action": "none",
        "target_id": None
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)