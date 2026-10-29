
"""Persistent subject management for PH Practice Hub."""

import re

from sqlalchemy import Boolean, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from database import Base


DEFAULT_SUBJECTS = [
    ("quant", "Quant"),
    ("reasoning", "Reasoning"),
    ("english", "English"),
    ("computer", "Computer"),
]


class Subject(Base):
    __tablename__ = "quiz_subjects"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    subject_id: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )


def normalize_subject_id(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")[:80]


def ensure_default_subjects(db: Session) -> None:
    changed = False

    for subject_id, name in DEFAULT_SUBJECTS:
        existing = db.scalar(
            select(Subject).where(
                Subject.subject_id == subject_id
            )
        )

        if existing is None:
            db.add(
                Subject(
                    subject_id=subject_id,
                    name=name,
                    active=True,
                )
            )
            changed = True

    if changed:
        db.commit()


def list_subjects(
    db: Session, include_inactive: bool = True
) -> list[Subject]:
    query = select(Subject).order_by(Subject.name)

    if not include_inactive:
        query = query.where(Subject.active.is_(True))

    return list(db.scalars(query).all())


def add_subject(
    db: Session, name: str
) -> tuple[Subject, str]:
    name = " ".join((name or "").split())

    if not name or len(name) > 100:
        raise ValueError(
            "Subject name must contain 1–100 characters."
        )

    subject_id = normalize_subject_id(name)

    if not subject_id:
        raise ValueError(
            "Enter a subject name containing letters or numbers."
        )

    existing = db.scalar(
        select(Subject).where(
            Subject.subject_id == subject_id
        )
    )

    if existing:
        if existing.active:
            raise ValueError(
                f"Subject already exists: {existing.name} "
                f"({existing.subject_id})."
            )

        existing.name = name
        existing.active = True
        db.commit()
        db.refresh(existing)
        return existing, "reactivated"

    duplicate = db.scalar(
        select(Subject).where(
            Subject.name.ilike(name)
        )
    )

    if duplicate:
        if duplicate.active:
            raise ValueError(
                f"Subject name already exists: {duplicate.name}."
            )

        duplicate.name = name
        duplicate.active = True
        db.commit()
        db.refresh(duplicate)
        return duplicate, "reactivated"

    subject = Subject(
        subject_id=subject_id,
        name=name,
        active=True,
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)

    return subject, "created"


def rename_subject(
    db: Session, subject_id: str, new_name: str
) -> Subject:
    subject_id = normalize_subject_id(subject_id)
    new_name = " ".join((new_name or "").split())

    if not new_name or len(new_name) > 100:
        raise ValueError(
            "New name must contain 1–100 characters."
        )

    subject = db.scalar(
        select(Subject).where(
            Subject.subject_id == subject_id
        )
    )

    if subject is None or not subject.active:
        raise ValueError(
            "Active subject not found. Use /subjects."
        )

    duplicate = db.scalar(
        select(Subject).where(
            Subject.name.ilike(new_name),
            Subject.subject_id != subject_id,
            Subject.active.is_(True),
        )
    )

    if duplicate:
        raise ValueError(
            f"Another subject already uses the name "
            f"{duplicate.name}."
        )

    subject.name = new_name
    db.commit()
    db.refresh(subject)

    return subject


def deactivate_subject(
    db: Session, subject_id: str
) -> Subject:
    subject_id = normalize_subject_id(subject_id)

    subject = db.scalar(
        select(Subject).where(
            Subject.subject_id == subject_id
        )
    )

    if subject is None or not subject.active:
        raise ValueError(
            "Active subject not found. Use /subjects."
        )

    subject.active = False
    db.commit()
    db.refresh(subject)

    return subject


def resolve_active_subject(
    db: Session, value: str
) -> Subject | None:
    value = " ".join(
        (value or "").strip().lower().replace("-", " ").split()
    )
    subject_id = normalize_subject_id(value)

    subject = db.scalar(
        select(Subject).where(
            Subject.subject_id == subject_id,
            Subject.active.is_(True),
        )
    )

    if subject:
        return subject

    return db.scalar(
        select(Subject).where(
            Subject.name.ilike(value),
            Subject.active.is_(True),
        )
    )
