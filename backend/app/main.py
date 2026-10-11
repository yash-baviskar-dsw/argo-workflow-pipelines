from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Integer, String, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import URL


def make_database_url() -> str | URL:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url
    return URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("DB_USER", "notesapp"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "notesdb"),
    )


DATABASE_URL = make_database_url()
engine_options: dict[str, object] = {"pool_pre_ping": True}
if isinstance(DATABASE_URL, str) and DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
        engine_options["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


def initialize_database() -> None:
    """Wait briefly for PostgreSQL on first boot, then create the demo table."""
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            return
        except SQLAlchemyError as exc:
            last_error = exc
            if attempt < 29:
                time.sleep(2)
    raise RuntimeError("Database did not become ready after 60 seconds") from last_error


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Workflow Lab API",
    description="A tiny API used to exercise an Argo Workflows CI/CD pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)


class NoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    created_at: datetime


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
def readyz(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@app.get("/version", tags=["metadata"])
def version() -> dict[str, str]:
    return {
        "service": "workflow-lab-api",
        "version": os.getenv("APP_VERSION", "dev"),
        "framework": "FastAPI",
    }


@app.get("/notes", response_model=list[NoteResponse], tags=["notes"])
def list_notes(db: Session = Depends(get_db)) -> list[Note]:
    return list(db.query(Note).order_by(Note.id.desc()).limit(50).all())


@app.post(
    "/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["notes"],
)
def create_note(payload: NoteCreate, db: Session = Depends(get_db)) -> Note:
    note = Note(text=payload.text)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note
