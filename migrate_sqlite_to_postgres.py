from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert

# Подключения
sqlite_url = "sqlite://///studio/stock/.TagStudio/ts_library.sqlite"
postgres_url = "postgresql+psycopg2://postgres:acescg@localhost/tagstudio_db"

# Движки и метаданные
sqlite_engine = create_engine(sqlite_url)
postgres_engine = create_engine(postgres_url)

sqlite_metadata = MetaData()
sqlite_metadata.reflect(bind=sqlite_engine)

# Создание таблиц в Postgres
sqlite_metadata.create_all(bind=postgres_engine)

# Очистка данных из Postgres
with postgres_engine.begin() as conn:
    for table in reversed(sqlite_metadata.sorted_tables):
        print(f"-> Clearing table: {table.name}")
        conn.execute(table.delete())

# Сессии
SqliteSession = sessionmaker(bind=sqlite_engine)
PostgresSession = sessionmaker(bind=postgres_engine)

sqlite_session = SqliteSession()
postgres_session = PostgresSession()

# Перенос данных
for table in sqlite_metadata.sorted_tables:
    print(f"-> Migrating table: {table.name}")
    rows = [
        dict(row._mapping)
        for row in sqlite_session.execute(table.select())
    ]
    if rows:
        postgres_session.execute(table.insert(), rows)
        print(f"   Inserted {len(rows)} rows.")

postgres_session.commit()
sqlite_session.close()
postgres_session.close()

print("✅ Migration completed successfully!")