
"""Owner-controlled Telegram group access management."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import ApprovedGroup


def approve_group(
    db: Session,
    chat_id: int | str,
    chat_title: str = "",
) -> ApprovedGroup:
    """Add a group to the approved list or reactivate it."""

    normalized_id = str(chat_id).strip()

    if not normalized_id:
        raise ValueError("A valid Telegram group ID is required.")

    group = db.scalar(
        select(ApprovedGroup).where(
            ApprovedGroup.chat_id == normalized_id
        )
    )

    if group is None:
        group = ApprovedGroup(
            chat_id=normalized_id,
            chat_title=chat_title[:200],
            active=True,
        )
        db.add(group)
    else:
        group.active = True
        if chat_title:
            group.chat_title = chat_title[:200]

    db.commit()
    db.refresh(group)
    return group


def revoke_group(
    db: Session,
    chat_id: int | str,
) -> bool:
    """Disable a group's permission to receive quizzes."""

    normalized_id = str(chat_id).strip()

    group = db.scalar(
        select(ApprovedGroup).where(
            ApprovedGroup.chat_id == normalized_id
        )
    )

    if group is None or not group.active:
        return False

    group.active = False
    db.commit()
    return True


def is_group_approved(
    db: Session,
    chat_id: int | str,
) -> bool:
    """Check the database allowlist before group publication."""

    normalized_id = str(chat_id).strip()

    if not normalized_id:
        return False

    return db.scalar(
        select(ApprovedGroup.id).where(
            ApprovedGroup.chat_id == normalized_id,
            ApprovedGroup.active.is_(True),
        )
    ) is not None


def list_approved_groups(db: Session) -> list[dict]:
    """Return currently active groups."""

    groups = db.scalars(
        select(ApprovedGroup)
        .where(ApprovedGroup.active.is_(True))
        .order_by(ApprovedGroup.chat_title)
    ).all()

    return [
        {
            "chat_id": group.chat_id,
            "chat_title": group.chat_title,
            "approved_at": (
                group.approved_at.isoformat()
                if group.approved_at
                else None
            ),
        }
        for group in groups
    ]
