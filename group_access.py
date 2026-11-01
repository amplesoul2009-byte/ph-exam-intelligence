
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

    from database import GroupJoinRequest

    pending_request = db.scalar(
        select(GroupJoinRequest).where(
            GroupJoinRequest.chat_id == normalized_id
        )
    )
    effective_title = chat_title or (
        pending_request.chat_title if pending_request is not None else ""
    )

    group = db.scalar(
        select(ApprovedGroup).where(
            ApprovedGroup.chat_id == normalized_id
        )
    )

    if group is None:
        group = ApprovedGroup(
            chat_id=normalized_id,
            chat_title=effective_title[:200],
            active=True,
        )
        db.add(group)
    else:
        group.active = True
        if effective_title:
            group.chat_title = effective_title[:200]

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


def request_group_access(
    db: Session,
    *,
    chat_id: int | str,
    chat_title: str,
    requester_user_id: int | str,
    requester_name: str = "",
):
    """Create or refresh a group access request from a group administrator."""
    from datetime import datetime, timezone
    from database import GroupJoinRequest

    normalized_id = str(chat_id).strip()
    if not normalized_id:
        raise ValueError("A valid Telegram group ID is required.")

    if is_group_approved(db, normalized_id):
        return None, "approved"

    request = db.scalar(
        select(GroupJoinRequest).where(
            GroupJoinRequest.chat_id == normalized_id
        )
    )

    if request is not None and request.status == "pending":
        return request, "pending"

    if request is None:
        request = GroupJoinRequest(
            chat_id=normalized_id,
            chat_title=(chat_title or "")[:200],
            requester_user_id=str(requester_user_id),
            requester_name=(requester_name or "")[:200],
            status="pending",
        )
        db.add(request)
    else:
        request.chat_title = (chat_title or "")[:200]
        request.requester_user_id = str(requester_user_id)
        request.requester_name = (requester_name or "")[:200]
        request.status = "pending"
        request.requested_at = datetime.now(timezone.utc)
        request.reviewed_at = None

    db.commit()
    db.refresh(request)
    return request, "created"


def list_pending_group_requests(db: Session) -> list[dict]:
    """Return group access requests awaiting owner review."""
    from database import GroupJoinRequest

    requests = db.scalars(
        select(GroupJoinRequest)
        .where(GroupJoinRequest.status == "pending")
        .order_by(GroupJoinRequest.requested_at)
    ).all()

    return [
        {
            "chat_id": request.chat_id,
            "chat_title": request.chat_title,
            "requester_user_id": request.requester_user_id,
            "requester_name": request.requester_name,
            "requested_at": (
                request.requested_at.isoformat()
                if request.requested_at else None
            ),
        }
        for request in requests
    ]


def review_group_request(
    db: Session,
    *,
    chat_id: int | str,
    decision: str,
) -> bool:
    """Mark a pending group request approved or rejected."""
    from datetime import datetime, timezone
    from database import GroupJoinRequest

    if decision not in {"approved", "rejected"}:
        raise ValueError("Decision must be approved or rejected.")

    request = db.scalar(
        select(GroupJoinRequest).where(
            GroupJoinRequest.chat_id == str(chat_id).strip()
        )
    )
    if request is None or request.status != "pending":
        return False

    request.status = decision
    request.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return True
