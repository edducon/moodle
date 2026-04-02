import uuid
from sqlalchemy import create_engine, Column, Integer, Boolean, String, DateTime, Text, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker, DeclarativeBase, relationship
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
    used_context = Column(Text)
    is_helpful = Column(Boolean, nullable=True)

class CourseParticipant(Base):
    __tablename__ = "course_participants"

    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(String, index=True)
    name = Column(String)
    role = Column(String)
    group_name = Column(String, nullable=True)

class CourseDeadline(Base):
    __tablename__ = "course_deadlines"

    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(String, index=True)
    moodle_id = Column(String, index=True)
    title = Column(String)
    due_date = Column(String)
    url = Column(String)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc))

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
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS course_participants (
            id SERIAL PRIMARY KEY,
            course_id VARCHAR,
            name VARCHAR,
            role VARCHAR,
            group_name VARCHAR
        )
    """))
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_course_participants_course_id
        ON course_participants (course_id)
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS course_deadlines (
            id SERIAL PRIMARY KEY,
            course_id VARCHAR,
            moodle_id VARCHAR,
            title VARCHAR,
            due_date VARCHAR,
            url VARCHAR,
            last_updated TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_course_deadlines_course_id
        ON course_deadlines (course_id)
    """))
    conn.commit()