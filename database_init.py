
"""Initialize all PH Greybot database tables."""

import logging

from database import Base, engine

# Import every model so SQLAlchemy registers its table metadata.
from database import (  # noqa: F401
    ApprovedGroup,
    AttemptAnswer,
    ImportBatch,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)

from participant_poll_attempt import ParticipantPollAttempt  # noqa: F401
from poll_registry import PollRegistry  # noqa: F401

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Create missing tables without deleting existing records."""

    Base.metadata.create_all(bind=engine)
    logger.info("PH Greybot database tables initialized.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    print("PH Greybot database initialization completed.")
