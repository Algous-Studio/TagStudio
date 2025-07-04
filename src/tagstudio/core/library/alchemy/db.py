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

def make_optimized_tables(engine: Engine) -> None:
    """
    Создание таблиц с оптимизированными индексами
    Специально настроено для производительности TagStudio
    """
    logger.info("[Library] Creating optimized DB tables...")
    create_search_optimized_indexes(engine)
    # Создание основных таблиц
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        # === КРИТИЧЕСКИЕ ИНДЕКСЫ ДЛЯ TAGSTUDIO ===
        # Индексы для таблицы entries (основная таблица файлов)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_path ON entries(path)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_filename ON entries(filename)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_date_added ON entries(date_added DESC)
        """))
        # Составной индекс для быстрого поиска
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_path_filename ON entries(path, filename)
        """))
        # === ИНДЕКСЫ ДЛЯ ТЕГОВ ===
        # Индексы для связей entry-tag (самые частые запросы)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_entry_id ON tag_entries(entry_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_tag_id ON tag_entries(tag_id)
        """))
        # Составной индекс для JOIN операций
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_composite ON tag_entries(entry_id, tag_id)
        """))
        # Индекс для имен тегов (поиск по тегам)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)
        """))
        # === ФИНАЛЬНАЯ ОПТИМИЗАЦИЯ ===
        # Обновление статистики для оптимизатора
        conn.execute(text("ANALYZE"))
        # Компактификация БД
        conn.execute(text("PRAGMA optimize"))
        conn.commit()
        logger.info("[Library] Optimized DB tables created successfully")
# def make_tables(engine: Engine) -> None:
#     logger.info("[Library] Creating DB tables...")
#     Base.metadata.create_all(engine)
#     # create_search_optimized_indexes(engine)
#     # tag IDs < 1000 are reserved
#     # create tag and delete it to bump the autoincrement sequence
#     # TODO - find a better way
#     # is this the better way?
#     with engine.connect() as conn:
#         result = conn.execute(text("SELECT SEQ FROM sqlite_sequence WHERE name='tags'"))
#         autoincrement_val = result.scalar()
#         if not autoincrement_val or autoincrement_val <= RESERVED_TAG_END:
#             try:
#                 conn.execute(
#                     text(
#                         "INSERT INTO tags "
#                         "(id, name, color_namespace, color_slug, is_category) VALUES "
#                         f"({RESERVED_TAG_END}, 'temp', NULL, NULL, false)"
#                     )
#                 )
#                 conn.execute(text(f"DELETE FROM tags WHERE id = {RESERVED_TAG_END}"))
#                 conn.commit()
#             except OperationalError as e:
#                 logger.error("Could not initialize built-in tags", error=e)
#                 conn.rollback()


def drop_tables(engine: Engine) -> None:
    logger.info("dropping db tables")
    Base.metadata.drop_all(engine)


def create_search_optimized_indexes(engine: Engine) -> None:
    """
    Создание индексов, специально оптимизированных для поиска
    КРИТИЧЕСКИ ВАЖНО для производительности поиска
    """
    logger.info("[Library] Creating search-optimized indexes...")
    with engine.connect() as conn:
        # === ИНДЕКСЫ ДЛЯ ПОИСКА ПО ИМЕНАМ ФАЙЛОВ ===
        # Быстрый поиск по имени файла (case-insensitive)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_filename_lower
            ON entries(LOWER(filename))
        """))
        # Быстрый поиск по пути (case-insensitive)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_path_lower
            ON entries(LOWER(path))
        """))
        # Составной индекс для сортировки по имени файла
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_filename_id
            ON entries(LOWER(filename), id)
        """))
        # Составной индекс для сортировки по пути
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_path_id
            ON entries(LOWER(path), id)
        """))
        # === ИНДЕКСЫ ДЛЯ ПОИСКА ПО ТЕГАМ ===
        # Быстрый поиск по тегам (критично для производительности)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_optimized
            ON tag_entries(tag_id, entry_id)
        """))
        # Обратный индекс для JOIN операций
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_reverse
            ON tag_entries(entry_id, tag_id)
        """))
        # Индекс для подсчета тегов у записи
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_entries_count
            ON tag_entries(entry_id, tag_id)
        """))
        # === ИНДЕКСЫ ДЛЯ ТЕГОВ ===
        # Быстрый поиск по имени тега (case-insensitive)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tags_name_lower
            ON tags(LOWER(name))
        """))# Индекс для алиасов тегов
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tag_aliases_name_lower
            ON tag_aliases(LOWER(name))
        """))
        # === ИНДЕКСЫ ДЛЯ ФИЛЬТРАЦИИ ===
        # Быстрая фильтрация по расширению файла
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_suffix
            ON entries(suffix)
        """))
        # Составной индекс для фильтрации + сортировки
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_suffix_filename
            ON entries(suffix, LOWER(filename))
        """))
        # === СТАТИСТИКА ===
        # Обновление статистики для оптимизатора
        conn.execute(text("ANALYZE"))
        conn.commit()
        logger.info("[Library] Search-optimized indexes created successfully")


def create_fts_search_tables(engine: Engine) -> None:
    """
    Создание Full-Text Search таблиц для мгновенного поиска
    """
    logger.info("[Library] Creating FTS5 search tables...")
    with engine.connect() as conn:
    # === СОЗДАНИЕ FTS5 ТАБЛИЦЫ ===
    # FTS таблица для поиска по файлам
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
            USING fts5(
                filename,
                path,
                content=entries,
                content_rowid=id,
                tokenize='porter ascii'
            )
        """))
        # FTS таблица для поиска по тегам
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tags_fts
            USING fts5(
                name,
                content=tags,
                content_rowid=id,
                tokenize='porter ascii'
            )
        """))
        # === ТРИГГЕРЫ ДЛЯ СИНХРОНИЗАЦИИ ===
        # Синхронизация entries_fts при INSERT
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS entries_fts_insert
            AFTER INSERT ON entries
            BEGIN
                INSERT INTO entries_fts(rowid, filename, path)
                VALUES (new.id, new.filename, new.path);
            END
        """))
        # Синхронизация entries_fts при UPDATE
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS entries_fts_update
            AFTER UPDATE ON entries
            BEGIN
                UPDATE entries_fts
                SET filename = new.filename, path = new.path
                WHERE rowid = new.id;
            END
        """))
        # Синхронизация entries_fts при DELETE
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS entries_fts_delete
            AFTER DELETE ON entries
            BEGIN
                DELETE FROM entries_fts WHERE rowid = old.id;
            END
        """))# === ЗАПОЛНЕНИЕ ДАННЫМИ ===
        # Заполнить FTS таблицы существующими данными
        conn.execute(text("""
            INSERT INTO entries_fts(rowid, filename, path)
            SELECT id, filename, path FROM entries
            WHERE NOT EXISTS (SELECT 1 FROM entries_fts WHERE rowid = entries.id)
        """))
        conn.execute(text("""
            INSERT INTO tags_fts(rowid, name)
            SELECT id, name FROM tags
            WHERE NOT EXISTS (SELECT 1 FROM tags_fts WHERE rowid = tags.id)
        """))
        conn.commit()
        logger.info("[Library] FTS5 search tables created successfully")