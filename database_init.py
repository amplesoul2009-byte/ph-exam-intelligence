
"""Initialize PH Practice Hub database tables safely."""

import logging

from sqlalchemy import inspect, text

from database import Base, engine
from database import (  # noqa: F401
    ApprovedGroup,
    AttemptAnswer,
    ImportBatch,
    BotSetting,
    GroupJoinRequest,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)
from participant_poll_attempt import ParticipantPollAttempt  # noqa: F401
from poll_registry import PollRegistry  # noqa: F401
from subjects import Subject  # noqa: F401

logger = logging.getLogger(__name__)


def _migrate_poll_registry() -> None:
    """Add option-order storage without deleting existing poll records."""

    inspector = inspect(engine)

    if "poll_registry" not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("poll_registry")
    }

    if "option_order" in columns:
        logger.info("Poll option-order column already exists.")
        return

    statement = text(
        "ALTER TABLE poll_registry "
        "ADD COLUMN option_order VARCHAR(4) "
        "NOT NULL DEFAULT 'ABCD'"
    )

    with engine.begin() as connection:
        connection.execute(statement)

    logger.info("Added poll option-order column safely.")


def _migrate_publication_columns() -> None:
    """Add publication IDs to old attempts and poll registry rows safely."""
    inspector = inspect(engine)
    statements = []
    for table_name in ("quiz_attempts", "poll_registry"):
        if table_name not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "publication_id" not in columns:
            statements.append(
                f"ALTER TABLE {table_name} ADD COLUMN publication_id VARCHAR(36) NULL"
            )
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        logger.info("Added publication_id columns safely.")


def initialize_database() -> None:
    """Create missing tables and apply safe additive migrations."""

    Base.metadata.create_all(bind=engine)
    _migrate_poll_registry()
    _migrate_publication_columns()

    logger.info("PH Practice Hub database tables initialized.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    print("PH Practice Hub database initialization completed.")
