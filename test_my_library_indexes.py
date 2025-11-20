#!/usr/bin/env python3
"""Test script to verify and create indexes on your existing library.

This script is SAFE - it only adds missing indexes, never removes data.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from tagstudio.core.library.alchemy.migrations import DatabaseMigration


def test_existing_library(connection_string: str):
    """Test indexes on an existing library.

    Args:
        connection_string: Database connection string
            Examples:
            - "postgresql+psycopg2://postgres:acescg@localhost/tagstudio_db"
            - "sqlite:///C:/path/to/library/.TagStudio/ts_library.db"
    """

    print("=" * 80)
    print("TagStudio Index Testing - Existing Library")
    print("=" * 80)
    print()
    print(f"Connecting to: {connection_string}")
    print()

    # Create engine
    engine = create_engine(connection_string)
    inspector = inspect(engine)

    # Check what indexes currently exist
    print("CURRENT STATE:")
    print("-" * 80)

    tables_to_check = ['entries', 'tags', 'tag_entries']

    for table_name in tables_to_check:
        print(f"\n{table_name}:")
        try:
            indexes = inspector.get_indexes(table_name)
            if indexes:
                for idx in indexes:
                    columns = ', '.join(idx['column_names'])
                    unique = " (unique)" if idx.get('unique', False) else ""
                    print(f"  • {idx['name']}: {columns}{unique}")
            else:
                print(f"  (no indexes)")
        except Exception as e:
            print(f"  Error: {e}")

    print()
    print("=" * 80)
    print()

    # Check for missing Priority 1 indexes
    expected_indexes = DatabaseMigration.REQUIRED_INDEXES

    missing = {}
    for table_name, required in expected_indexes.items():
        existing = DatabaseMigration.get_existing_indexes(engine, table_name)
        missing_in_table = []

        for index_name, columns, unique in required:
            if index_name not in existing:
                missing_in_table.append((index_name, columns, unique))

        if missing_in_table:
            missing[table_name] = missing_in_table

    if missing:
        print("MISSING INDEXES:")
        print("-" * 80)
        for table_name, missing_indexes in missing.items():
            print(f"\n{table_name}:")
            for index_name, columns, unique in missing_indexes:
                columns_str = ', '.join(columns)
                print(f"  ❌ {index_name}: {columns_str}")

        print()
        print("=" * 80)
        print()

        # Ask user if they want to create indexes
        response = input("Do you want to create these missing indexes? (yes/no): ").strip().lower()

        if response in ['yes', 'y']:
            print()
            print("Creating missing indexes...")
            print("-" * 80)

            created = DatabaseMigration.migrate_and_analyze(engine)

            if created:
                total_created = sum(len(indexes) for indexes in created.values())
                print()
                print(f"✓ Created {total_created} new indexes!")
                print()
                print("Tables updated:")
                for table_name, index_names in created.items():
                    print(f"  • {table_name}: {', '.join(index_names)}")

                print()
                print("✓ Database statistics updated (ANALYZE)")
                print()
                print("=" * 80)
                print()
                print("PERFORMANCE IMPROVEMENTS EXPECTED:")
                print("-" * 80)
                print("  • Folder queries: 30-60s → < 1s")
                print("  • File type filtering (filetype:jpg): 30-60s → < 1s")
                print("  • Sequence filtering: 30-60s → < 0.5s")
                print("  • Tag searches: 10-30s → < 1s")
                print("  • Multi-tag queries: 10-30s → < 1s")
                print()
            else:
                print("✓ No indexes needed to be created")

        else:
            print()
            print("Skipped index creation.")
            print()
            print("To create them later, run:")
            print("  from tagstudio.core.library.alchemy.migrations import migrate_database_schema")
            print("  from sqlalchemy import create_engine")
            print(f"  engine = create_engine('{connection_string}')")
            print("  migrate_database_schema(engine)")

    else:
        print("✓ All Priority 1 indexes are present!")
        print()
        print("Your library is optimized for 10M+ files.")
        print()
        print("If you've recently added >100K entries, you may want to update statistics:")
        print("  library.analyze_database()")

    print()
    print("=" * 80)


if __name__ == "__main__":
    print()
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "TagStudio Index Testing" + " " * 35 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    # Your database connection string
    CONNECTION_STRING = "postgresql+psycopg2://postgres:acescg@localhost/tagstudio_db"

    print("Testing your library at:")
    print(f"  {CONNECTION_STRING}")
    print()
    print("This script will:")
    print("  1. Check which indexes exist")
    print("  2. Show which Priority 1 indexes are missing")
    print("  3. Ask if you want to create them")
    print()

    response = input("Continue? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        print()
        test_existing_library(CONNECTION_STRING)
    else:
        print()
        print("Cancelled.")
        print()
