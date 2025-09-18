# Copyright (C) 2025
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio


from pathlib import Path

import structlog
from sqlalchemy import Dialect, Engine, String, TypeDecorator, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase

from tagstudio.core.constants import RESERVED_TAG_END

logger = structlog.getLogger(__name__)


class PathType(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value: Path, dialect: Dialect):
        if value is not None:
            return Path(value).as_posix()
        return None

    def process_result_value(self, value: str, dialect: Dialect):
        if value is not None:
            return Path(value)
        return None


class Base(DeclarativeBase):
    type_annotation_map = {Path: PathType}


def make_engine(connection_string: str) -> Engine:
    return create_engine(connection_string)


def make_tables(engine: Engine) -> None:
    logger.info("[Library] Creating DB tables...")
    Base.metadata.create_all(engine)

    # tag IDs < 1000 are reserved
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT MAX(id) FROM tags"))
            max_id = result.scalar() or 0

            next_id = max(max_id + 1, RESERVED_TAG_END + 1)
            
            conn.execute(
                text(f"ALTER SEQUENCE tags_id_seq RESTART WITH {next_id}")
            )
            conn.commit()
            logger.info(f"[Library] Set tags sequence to start from {next_id}")
        except OperationalError as e:
            logger.error("Could not initialize tag sequence", error=e)
            conn.rollback()


def drop_tables(engine: Engine) -> None:
    logger.info("dropping db tables")
    Base.metadata.drop_all(engine)
