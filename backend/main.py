from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import datetime, timezone

from database import SessionLocal, Course
from config import settings

app = FastAPI(title="Moodle Assistant API")

origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class CourseData(BaseModel):
    course_id: str
    title: str
    sections: List[Dict[str, Any]]

@app.post("/api/course/sync")
def sync_course(data: CourseData, db: Session = Depends(get_db)):
    db_course = db.query(Course).filter(Course.course_id == data.course_id).first()

    if db_course:
        db_course.title = data.title
        db_course.content = data.sections
        db_course.last_updated = datetime.now(timezone.utc)
    else:
        db_course = Course(
            course_id=data.course_id,
            title=data.title,
            content=data.sections
        )
        db.add(db_course)

    db.commit()
    return {"status": "success", "message": f"Курс '{data.title}' сохранен/обновлен в БД!"}

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