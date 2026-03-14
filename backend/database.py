from sqlalchemy import create_engine, Column, String, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from datetime import datetime, timezone

# Импортируем векторный тип
from pgvector.sqlalchemy import Vector

from config import settings

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

class Course(Base):
    __tablename__ = "courses"
    course_id = Column(String, primary_key=True, index=True)
    title = Column(String)
    content = Column(JSONB)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# Таблица для хранения векторов модулей
class ModuleIndex(Base):
    __tablename__ = "module_index"
    moodle_id = Column(String, primary_key=True, index=True)
    course_id = Column(String, index=True)
    title = Column(String)
    content_text = Column(Text)
    # 384 - это размерность векторов нашей модели paraphrase-multilingual
    embedding = Column(Vector(384))

# Автоматически включаем расширение vector в PostgreSQL перед созданием таблиц
with engine.connect() as conn:
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    conn.commit()

Base.metadata.create_all(bind=engine)