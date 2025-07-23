from pathlib import Path
import pytest
from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Entry


def test_lazy_sequence_detection(tmp_path):
    """Sequences should be detected lazily only when accessed."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    # Add sequence entries
    entries = [
        Entry(folder=lib.folder, path=Path(f"shot_{i:04}.png"), fields=lib.default_fields)
        for i in range(1, 4)
    ]
    lib.add_entries(entries)
    
    registry = lib.sequence_registry
    
    # Cache should be empty initially
    assert len(registry._sequence_cache) == 0
    
    # Access first entry - should load entire sequence
    first_entry = entries[0]
    sequence = registry.get_complete_sequence(first_entry)
    
    assert len(sequence) == 3
    assert len(registry._sequence_cache) == 3  # All entries cached


def test_sequence_aware_pagination(tmp_path):
    """Pagination should group sequences correctly."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    # Create mixed content: sequences and single files
    entries = []
    # Sequence A (3 frames)
    for i in range(1, 4):
        entries.append(Entry(folder=lib.folder, path=Path(f"seqA_{i:04}.png"), fields=lib.default_fields))
    
    # Single file
    entries.append(Entry(folder=lib.folder, path=Path("single.jpg"), fields=lib.default_fields))
    
    # Sequence B (2 frames)  
    for i in range(1, 3):
        entries.append(Entry(folder=lib.folder, path=Path(f"seqB_{i:04}.png"), fields=lib.default_fields))
    
    lib.add_entries(entries)
    
    registry = lib.sequence_registry
    display_items, frame_counts = registry.get_sequence_aware_page(0, 10)
    
    # Should show: seqA poster, single file, seqB poster = 3 items
    assert len(display_items) == 3
    assert frame_counts[0] == 3  # seqA
    assert frame_counts[1] is None  # single file
    assert frame_counts[2] == 2  # seqB


def test_cache_eviction(tmp_path):
    """Cache should evict old entries when full."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success
    
    registry = lib.sequence_registry
    registry._cache_max_size = 2  # Small cache for testing
    
    # Add entries
    entries = [
        Entry(folder=lib.folder, path=Path(f"file_{i}.png"), fields=lib.default_fields)
        for i in range(5)
    ]
    lib.add_entries(entries)
    
    # Access entries to fill cache
    for entry in entries[:3]:
        registry.get_complete_sequence(entry)
    
    # Cache should be limited to max_size
    assert len(registry._sequence_cache) <= registry._cache_max_size


def test_ids_for_poster(tmp_path):
    """ids_for_poster should return all frame IDs for a sequence."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    entries = [
        Entry(folder=lib.folder, path=Path(f"sequence_{i:04}.png"), fields=lib.default_fields)
        for i in range(1, 4)
    ]
    lib.add_entries(entries)
    
    registry = lib.sequence_registry
    
    # Get sequence for first entry (poster)
    poster_entry = entries[0]
    sequence = registry.get_complete_sequence(poster_entry)
    poster = min(sequence, key=lambda e: e.path)
    
    # Should return all frame IDs
    frame_ids = registry.ids_for_poster(poster.id)
    expected_ids = [e.id for e in sequence]
    
    assert set(frame_ids) == set(expected_ids)


def test_non_sequence_files(tmp_path):
    """Non-sequence files should be handled correctly."""
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    # Add non-sequence files
    entries = [
        Entry(folder=lib.folder, path=Path("document.pdf"), fields=lib.default_fields),
        Entry(folder=lib.folder, path=Path("image.jpg"), fields=lib.default_fields),
    ]
    lib.add_entries(entries)
    
    registry = lib.sequence_registry
    
    for entry in entries:
        sequence = registry.get_complete_sequence(entry)
        assert len(sequence) == 1
        assert sequence[0] == entry
        
        frame_ids = registry.ids_for_poster(entry.id)
        assert frame_ids == [entry.id]


def test_clear_cache(tmp_path):
    """clear_cache should empty the cache completely.""" 
    lib = Library()
    status = lib.open_library(tmp_path, ":memory:")
    assert status.success

    entries = [
        Entry(folder=lib.folder, path=Path(f"seq_{i:04}.png"), fields=lib.default_fields)
        for i in range(1, 4)
    ]
    lib.add_entries(entries)
    
    registry = lib.sequence_registry
    
    # Fill cache
    registry.get_complete_sequence(entries[0])
    assert len(registry._sequence_cache) > 0
    
    # Clear cache
    registry.clear_cache()
    assert len(registry._sequence_cache) == 0
    assert len(registry._cache_access_order) == 0