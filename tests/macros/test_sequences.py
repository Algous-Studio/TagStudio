from pathlib import Path

from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Entry


def test_sequence_detection(tmp_path):
    """Files with incrementing frame numbers should form a sequence."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    e1 = Entry(folder=lib.folder, path=Path("shot_0001.png"), fields=lib.default_fields)
    e2 = Entry(folder=lib.folder, path=Path("shot_0002.png"), fields=lib.default_fields)
    e3 = Entry(folder=lib.folder, path=Path("shot_0003.png"), fields=lib.default_fields)
    lib.add_entries([e1, e2, e3])
    progress = list(lib.refresh_sequences())
    assert progress == list(range(lib.entries_count))
    assert lib.sequence_registry.sequences_count == 1
    seq = lib.sequence_registry.sequences[0]
    assert seq.frame_count == 3
    assert seq.poster.path == Path("shot_0001.png")


def test_multiple_sequences_and_ids(tmp_path):
    """Multiple sequences should be detected separately and ids_for_poster returns all frame ids."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    entries = [
        Entry(folder=lib.folder, path=Path(f"shotA_{i:04}.png"), fields=lib.default_fields)
        for i in range(3)
    ] + [
        Entry(folder=lib.folder, path=Path(f"shotB_{i:04}.png"), fields=lib.default_fields)
        for i in range(2)
    ]
    lib.add_entries(entries)
    list(lib.refresh_sequences())

    assert lib.sequence_registry.sequences_count == 2

    first_seq = lib.sequence_registry.sequences[0]
    ids = lib.sequence_registry.ids_for_poster(first_seq.poster.id)
    assert set(ids) == {e.id for e in first_seq.entries}