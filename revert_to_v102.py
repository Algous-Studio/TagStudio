#!/usr/bin/env python3
"""
Script to revert TagStudio database from v103 to v102.
This is useful if you need to use an older version of TagStudio code.
"""

from sqlalchemy import create_engine, text

# Windows local database connection
connection_string = "postgresql+psycopg2://postgres:acescg@localhost/tagstudio_db"

print("Connecting to database...")
engine = create_engine(connection_string)

try:
    with engine.connect() as conn:
        print("Connected successfully!")

        # Check current version
        result = conn.execute(text("SELECT key, value FROM preferences WHERE key = 'DB_VERSION'"))
        current = result.fetchone()
        print(f"Current DB_VERSION in preferences: {current}")

        result = conn.execute(text("SELECT key, value FROM versions WHERE key = 'CURRENT'"))
        current_ver = result.fetchone()
        print(f"Current version in versions table: {current_ver}")

        # Update to v102
        print("\nReverting to v102...")
        conn.execute(text("UPDATE preferences SET value = '102' WHERE key = 'DB_VERSION'"))
        conn.execute(text("UPDATE versions SET value = '102' WHERE key = 'CURRENT'"))
        conn.commit()

        # Verify the change
        result = conn.execute(text("SELECT key, value FROM preferences WHERE key = 'DB_VERSION'"))
        new_val = result.fetchone()
        print(f"New DB_VERSION in preferences: {new_val}")

        result = conn.execute(text("SELECT key, value FROM versions WHERE key = 'CURRENT'"))
        new_ver = result.fetchone()
        print(f"New version in versions table: {new_ver}")

        print("\n✓ Database successfully reverted to v102!")
        print("You can now run TagStudio v102 code with this database.")

except Exception as e:
    print(f"Error: {e}")
    print("\nIf you see a connection error, please verify:")
    print("1. PostgreSQL is running on Windows")
    print("2. The password 'acescg' is correct for user 'postgres'")
    print("3. The database 'tagstudio_db' exists")
