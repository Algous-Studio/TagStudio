# Copyright (C) 2025
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio

"""Database migration utilities for TagStudio.

Handles incremental schema updates for existing databases.
"""

import structlog
from sqlalchemy import Engine, inspect, text

logger = structlog.get_logger(__name__)


class DatabaseMigration:
    """Manages database schema migrations."""

    # Define all indexes that should exist
    # Format: {table_name: [(index_name, columns, unique)]}
    REQUIRED_INDEXES = {
        'entries': [
            ('ix_entries_folder_id', ['folder_id'], False),
            ('ix_entries_suffix', ['suffix'], False),
            ('ix_entries_is_sequence', ['is_sequence'], False),
            ('ix_entries_folder_sequence', ['folder_id', 'is_sequence'], False),
        ],
        'tags': [
            ('ix_tags_name', ['name'], False),
        ],
        'tag_entries': [
            ('ix_tag_entries_entry_id', ['entry_id'], False),
        ],
    }

    @staticmethod
    def get_existing_indexes(engine: Engine, table_name: str) -> set[str]:
        """Get list of existing index names for a table.

        Args:
            engine: SQLAlchemy engine
            table_name: Name of the table to check

        Returns:
            Set of index names that exist on the table
        """
        inspector = inspect(engine)
        try:
            indexes = inspector.get_indexes(table_name)
            return {idx['name'] for idx in indexes}
        except Exception as e:
            logger.error("Failed to get indexes", table=table_name, error=e)
            return set()

    @staticmethod
    def create_index_sql(
        index_name: str,
        table_name: str,
        columns: list[str],
        unique: bool = False
    ) -> str:
        """Generate SQL to create an index.

        Args:
            index_name: Name of the index
            table_name: Table to create index on
            columns: List of column names
            unique: Whether this is a unique index

        Returns:
            SQL CREATE INDEX statement
        """
        unique_str = "UNIQUE " if unique else ""
        columns_str = ", ".join(columns)
        return f"CREATE {unique_str}INDEX IF NOT EXISTS {index_name} ON {table_name}({columns_str})"

    @classmethod
    def migrate_indexes(cls, engine: Engine, show_progress: bool = True) -> dict[str, list[str]]:
        """Create missing indexes on existing database.

        Args:
            engine: SQLAlchemy engine
            show_progress: Whether to log progress

        Returns:
            Dictionary mapping table names to lists of created index names
        """
        created_indexes = {}

        logger.info("Starting index migration...")

        with engine.connect() as conn:
            for table_name, required_indexes in cls.REQUIRED_INDEXES.items():
                # Get existing indexes
                existing = cls.get_existing_indexes(engine, table_name)

                # Track what we create for this table
                table_created = []

                for index_name, columns, unique in required_indexes:
                    if index_name not in existing:
                        # Create the index
                        sql = cls.create_index_sql(index_name, table_name, columns, unique)

                        if show_progress:
                            logger.info(
                                "Creating index",
                                index=index_name,
                                table=table_name,
                                columns=columns
                            )

                        try:
                            conn.execute(text(sql))
                            table_created.append(index_name)
                        except Exception as e:
                            logger.error(
                                "Failed to create index",
                                index=index_name,
                                table=table_name,
                                error=e
                            )
                    else:
                        if show_progress:
                            logger.debug("Index already exists", index=index_name, table=table_name)

                if table_created:
                    created_indexes[table_name] = table_created

            # Commit all index creations
            conn.commit()

        if created_indexes:
            total_created = sum(len(indexes) for indexes in created_indexes.values())
            logger.info(
                "Index migration complete",
                total_created=total_created,
                tables=list(created_indexes.keys())
            )
        else:
            logger.info("No indexes needed to be created - database is up to date")

        return created_indexes

    @classmethod
    def analyze_tables(cls, engine: Engine, tables: list[str] | None = None) -> None:
        """Update database statistics for query optimization.

        Should be run after:
        - Creating new indexes
        - Bulk insert/update operations (>100K rows)
        - Major schema changes

        Args:
            engine: SQLAlchemy engine
            tables: Specific tables to analyze, or None for all tables
        """
        logger.info("Updating database statistics...")

        with engine.connect() as conn:
            if tables:
                # Analyze specific tables
                for table in tables:
                    try:
                        # SQLite and PostgreSQL both support ANALYZE <table>
                        conn.execute(text(f"ANALYZE {table}"))
                        logger.debug("Analyzed table", table=table)
                    except Exception as e:
                        logger.error("Failed to analyze table", table=table, error=e)
            else:
                # Analyze all tables
                try:
                    conn.execute(text("ANALYZE"))
                    logger.debug("Analyzed all tables")
                except Exception as e:
                    logger.error("Failed to analyze database", error=e)

            conn.commit()

        logger.info("Database statistics updated")

    @classmethod
    def migrate_and_analyze(cls, engine: Engine) -> dict[str, list[str]]:
        """Convenience method: Migrate indexes and analyze tables.

        Args:
            engine: SQLAlchemy engine

        Returns:
            Dictionary of created indexes by table
        """
        # Create missing indexes
        created_indexes = cls.migrate_indexes(engine, show_progress=True)

        # If we created any indexes, analyze those tables
        if created_indexes:
            tables_to_analyze = list(created_indexes.keys())
            cls.analyze_tables(engine, tables=tables_to_analyze)

        return created_indexes


def migrate_database_schema(engine: Engine, auto_analyze: bool = True) -> dict[str, list[str]]:
    """Main entry point for database migrations.

    Call this when opening a library to ensure schema is up to date.

    Args:
        engine: SQLAlchemy engine
        auto_analyze: Whether to automatically run ANALYZE after creating indexes

    Returns:
        Dictionary of created indexes by table

    Example:
        >>> from tagstudio.core.library.alchemy.library import Library
        >>> lib = Library()
        >>> lib.open_library(Path("/path/to/library"))
        >>> created = migrate_database_schema(lib.engine)
        >>> if created:
        >>>     print(f"Created {sum(len(v) for v in created.values())} new indexes")
    """
    if auto_analyze:
        return DatabaseMigration.migrate_and_analyze(engine)
    else:
        return DatabaseMigration.migrate_indexes(engine)
