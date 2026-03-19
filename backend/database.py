import uuid
from sqlalchemy import create_engine, Column, Integer, Boolean, String, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from datetime import datetime, timezone

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

class ModuleIndex(Base):
    __tablename__ = "module_index"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    moodle_id = Column(String, index=True)
    course_id = Column(String, index=True)
    module_type = Column(String, index=True)
    title = Column(String, index=True)
    content_text = Column(Text)
    url = Column(String)
    visibility = Column(JSONB)
    embedding = Column(Vector(384))

class ChatLog(Base):
    __tablename__ = "chat_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    course_id = Column(String(50), index=True)
    viewer_role = Column(String(50))
    user_query = Column(Text)
    ai_reply = Column(Text)
    used_context = Column(Text) # Сохраняем, на какие куски текста ИИ опирался
    is_helpful = Column(Boolean, nullable=True)

with engine.connect() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.commit()

Base.metadata.create_all(bind=engine)

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE module_index
        ADD COLUMN IF NOT EXISTS module_type VARCHAR
    """))
    conn.execute(text("""
        ALTER TABLE module_index
        ADD COLUMN IF NOT EXISTS visibility JSONB
    """))
    conn.commit()