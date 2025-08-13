#!/usr/bin/env python3

"""
Этот код предназначен для обновления библиотеки TagStudio, добавляя поддержку секвенций.
Он проверяет наличие файлов с расширениями, которые могут быть частью секвенции,
и обновляет соответствующие записи в базе данных, устанавливая флаг is_sequence.

library_path должен указывать на путь к библиотеке TagStudio. Сменить, если нужно.
"""
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy.orm import Session
from sqlalchemy import update, text, inspect

from tagstudio.core.library.alchemy.models import Entry
from tagstudio.core.library.alchemy.library import Library

SEQUENCE_EXTENSIONS = {"dpx", "exr", "jpg", "jpeg", "png", "tif", "tiff", "ari", "tga"}
SEQUENCE_RE = re.compile(r"^(.*?)(?:[._-]?)(\d{3,6})$")


class SequenceEntry:
    def __init__(self):
        self.entries = []

    @property
    def poster(self):
        return min(self.entries, key=lambda e: e.path) if self.entries else None

    @property
    def frame_count(self):
        return len(self.entries)


class SequenceRegistry:
    def __init__(self, library: Library):
        self.library = library
        self.sequences = []
        self.entry_to_sequence = {}

    def refresh_sequences(self):
        groups = defaultdict(SequenceEntry)

        for entry in self.library.get_entries():
            if entry.suffix.lower() not in SEQUENCE_EXTENSIONS:
                continue

            match = SEQUENCE_RE.match(entry.path.stem)
            if match:
                base = match.group(1)
                key = (entry.path.parent, base, entry.suffix)
                groups[key].entries.append(entry)

        self.sequences.clear()
        for seq in groups.values():
            if len(seq.entries) > 1:
                sorted_entries = sorted(seq.entries, key=lambda e: e.path)
                new_seq = SequenceEntry()
                new_seq.entries = sorted_entries
                self.sequences.append(new_seq)
                for e in sorted_entries:
                    self.entry_to_sequence[e.id] = new_seq


def ensure_is_sequence_column(library: Library):
    """Проверяет, есть ли колонка is_sequence в таблице entries. Если нет — создаёт."""
    insp = inspect(library.engine)
    columns = [col['name'] for col in insp.get_columns("entries")]

    if "is_sequence" not in columns:
        print("[INFO] Добавляем колонку is_sequence в таблицу entries...")
        with Session(library.engine) as session:
            session.execute(
                text("ALTER TABLE entries ADD COLUMN is_sequence BOOLEAN DEFAULT FALSE NOT NULL")
            )
            session.commit()
        print("[OK] Колонка is_sequence добавлена")
    else:
        print("[OK] Колонка is_sequence уже существует")


def update_sequences(library: Library):
    ensure_is_sequence_column(library)

    registry = SequenceRegistry(library)
    registry.refresh_sequences()
    print("AAAA")
    with Session(library.engine) as session:
        session.execute(update(Entry).values(is_sequence=False))
        session.commit()

        updated_count = 0
        for seq in registry.sequences:
            for i, entry in enumerate(seq.entries):
                session.execute(
                    update(Entry)
                    .where(Entry.id == entry.id)
                    .values(is_sequence=(i != 0))
                )

                updated_count += 1
        session.commit()

    print(f"[OK] Обновлено {len(registry.sequences)} секвенций, {updated_count} файлов")


if __name__ == "__main__":
    library_path = Path("/studio/stock")

    lib = Library()
    status = lib.open_library(library_path)

    if not status.success:
        print("[ERROR] Не удалось открыть библиотеку:", status.message or "Неизвестная ошибка")
    else:
        update_sequences(lib)
        lib.close()




"""
Этот закомментированный код использовать, если раскомментированный код не работает.
Но с ним нужно будет вручную добавить в таблицу entries колонку is_sequence.
tagstudio_db=# ALTER TABLE entries ADD COLUMN is_sequence BOOLEAN DEFAULT FALSE NOT NULL;
Хотя существующий код протестирован и работает.
"""




# #!/usr/bin/env python3
# import re
# from collections import defaultdict
# from pathlib import Path
# from sqlalchemy.orm import Session
# from sqlalchemy import update

# from tagstudio.core.library.alchemy.models import Entry
# from tagstudio.core.library.alchemy.library import Library

# SEQUENCE_EXTENSIONS = {"dpx", "exr", "jpg", "jpeg", "png", "tif", "tiff"}

# SEQUENCE_RE = re.compile(r"^(.*?)(?:[._-]?)(\d{3,6})$")


# class SequenceEntry:
#     def __init__(self):
#         self.entries = []

#     @property
#     def poster(self):
#         return min(self.entries, key=lambda e: e.path) if self.entries else None

#     @property
#     def frame_count(self):
#         return len(self.entries)


# class SequenceRegistry:
#     def __init__(self, library: Library):
#         self.library = library
#         self.sequences = []
#         self.entry_to_sequence = {}

#     def refresh_sequences(self):
#         groups = defaultdict(SequenceEntry)
#         print(1)
#         for entry in self.library.get_entries():
#             if entry.suffix.lower() not in SEQUENCE_EXTENSIONS:
#                 continue

#             match = SEQUENCE_RE.match(entry.path.stem)
#             if match:
#                 base = match.group(1)
#                 key = (entry.path.parent, base, entry.suffix)
#                 groups[key].entries.append(entry)
#         print(2)
#         self.sequences.clear()
#         for seq in groups.values():
#             if len(seq.entries) > 1:
#                 sorted_entries = sorted(seq.entries, key=lambda e: e.path)
#                 new_seq = SequenceEntry()
#                 new_seq.entries = sorted_entries
#                 self.sequences.append(new_seq)
#                 for e in sorted_entries:
#                     self.entry_to_sequence[e.id] = new_seq
        


# def update_sequences(library: Library):
#     registry = SequenceRegistry(library)
#     registry.refresh_sequences()
#     print(3)
#     with Session(library.engine) as session:
#         session.execute(update(Entry).values(is_sequence=False))
#         session.commit()

#         updated_count = 0
#         for seq in registry.sequences:
#             for i, entry in enumerate(seq.entries):
#                 session.execute(
#                     update(Entry)
#                     .where(Entry.id == entry.id)
#                     .values(is_sequence=(i != 0))
#                 )
#                 updated_count += 1
#         session.commit()

#     print(f"[OK] Обновлено {len(registry.sequences)} секвенций, {updated_count} файлов")


# if __name__ == "__main__":
#     # library_path = Path("/home/timur/Dev/library_tagstudio_test")
#     library_path = Path("/studio/stock")


#     lib = Library()
#     status = lib.open_library(library_path)

#     if not status.success:
#         print("[ERROR] Не удалось открыть библиотеку:", status.message or "Неизвестная ошибка")
#     else:
#         update_sequences(lib)
#         lib.close()
