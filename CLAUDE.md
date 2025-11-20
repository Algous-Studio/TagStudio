# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TagStudio is a photo & file organization application with a tag-based metadata system. It's a PySide6 (Qt) desktop application that manages libraries of files with custom tags, fields, and metadata. The project recently migrated from JSON to SQLAlchemy-based SQLite/PostgreSQL backend (v9.5+).

**Key Design Principles:**
- Never touch, move, or mess with user files (except explicit "Delete" operations)
- Maintain backward compatibility with user libraries across versions
- **Support large libraries (10,000,000+ files, several terabytes)**
- Cross-platform support: Windows 10+, macOS 13.0+, Linux

---

## ⚠️ CRITICAL: 10 Million File Optimization Requirement

**ALL code contributions MUST be optimized for libraries containing 10,000,000+ files.**

This is a non-negotiable requirement. Every function, query, and UI component must be designed with this scale in mind. Code that works fine for 1,000 files but degrades at 10M files is unacceptable.

### Mandatory Optimization Patterns

#### 1. NEVER Load All Entries Into Memory
```python
# ❌ FORBIDDEN - Will crash with 10M files
all_entries = session.query(Entry).all()
for entry in all_entries:
    process(entry)

# ✅ REQUIRED - Stream with yield_per
for entry in session.query(Entry).yield_per(1000):
    process(entry)

# ✅ REQUIRED - Paginated access
entries, total = library.get_entries_paginated(page=0, page_size=100)
```

#### 2. ALWAYS Use Batch Operations
```python
# ❌ FORBIDDEN - 10M individual inserts
for item in items:
    session.add(Entry(**item))
    session.commit()

# ✅ REQUIRED - Batch insert (10,000 per batch)
for chunk in chunked(items, 10000):
    session.bulk_insert_mappings(Entry, chunk)
    session.commit()
```

#### 3. ALWAYS Add Database Indexes
```python
# ✅ REQUIRED - Index all columns used in WHERE, JOIN, ORDER BY
class Entry(Base):
    __table_args__ = (
        Index('ix_entries_folder_id', 'folder_id'),
        Index('ix_entries_path_hash', 'path', postgresql_using='hash'),
        Index('ix_entries_folder_sequence', 'folder_id', 'is_sequence'),
    )
```

#### 4. NEVER Block the UI Thread
```python
# ❌ FORBIDDEN - Freezes UI during long operations
def on_button_click(self):
    results = library.search_all_entries(query)  # Takes 30 seconds
    self.display(results)

# ✅ REQUIRED - Background thread with signals
def on_button_click(self):
    self.searcher = AsyncSearcher(library)
    self.searcher.results_ready.connect(self.display)
    self.searcher.search(query)
```

#### 5. ALWAYS Use Virtual Scrolling for Lists
```python
# ❌ FORBIDDEN - Creates 10M widgets
for entry_id in all_entry_ids:
    widget = ThumbnailWidget(entry_id)
    layout.addWidget(widget)

# ✅ REQUIRED - Virtual model with on-demand loading
class VirtualEntryModel(QAbstractListModel):
    def data(self, index, role):
        # Load only visible rows from database
        if index.row() not in self._cache:
            self._load_range(index.row())
        return self._cache.get(index.row())
```

#### 6. ALWAYS Cache Expensive Operations
```python
# ❌ FORBIDDEN - Repeated expensive queries
def get_tag_count(tag_id):
    return session.query(func.count(EntryTag.entry_id)).filter_by(tag_id=tag_id).scalar()

# ✅ REQUIRED - Cached with TTL
from cachetools import TTLCache
_tag_count_cache = TTLCache(maxsize=10000, ttl=300)

def get_tag_count_cached(tag_id):
    if tag_id not in _tag_count_cache:
        _tag_count_cache[tag_id] = session.query(...).scalar()
    return _tag_count_cache[tag_id]
```

#### 7. ALWAYS Use Bloom Filters for Existence Checks
```python
# ❌ FORBIDDEN - 10M filesystem checks
def has_thumbnail(filepath):
    return Path(thumb_path).exists()  # Slow for 10M files

# ✅ REQUIRED - Bloom filter for fast negative checks
from pybloom_live import ScalableBloomFilter
_thumb_bloom = ScalableBloomFilter(initial_capacity=1000000, error_rate=0.001)

def has_thumbnail_fast(filepath):
    cache_hash = compute_hash(filepath)
    if cache_hash not in _thumb_bloom:
        return False  # Definite no
    return Path(thumb_path).exists()  # Verify positive
```

#### 8. ALWAYS Use Connection Pooling
```python
# ✅ REQUIRED - Proper connection pool configuration
engine = create_engine(
    db_url,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_pre_ping=True
)
```

#### 9. ALWAYS Configure SQLite for Performance
```python
# ✅ REQUIRED - SQLite pragmas for 10M files
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA cache_size=-500000"))  # 500MB
    conn.execute(text("PRAGMA mmap_size=2147483648"))  # 2GB
    conn.execute(text("PRAGMA synchronous=NORMAL"))
    conn.execute(text("PRAGMA temp_store=MEMORY"))
```

#### 10. ALWAYS Use Parallel Processing for Batch Operations
```python
# ❌ FORBIDDEN - Sequential processing
for filepath in filepaths:  # 10M files = hours
    generate_thumbnail(filepath)

# ✅ REQUIRED - Parallel with ProcessPoolExecutor
from concurrent.futures import ProcessPoolExecutor

with ProcessPoolExecutor(max_workers=8) as executor:
    for chunk in chunked(filepaths, 5000):
        futures = [executor.submit(generate_thumbnail, fp) for fp in chunk]
        for future in futures:
            future.result()
```

### Performance Targets

| Operation | Target for 10M Files |
|-----------|---------------------|
| Library open | < 5 seconds |
| Initial grid display | < 1 second |
| Search query | < 3 seconds |
| Tag application (batch) | < 10 seconds for 100K files |
| Directory scan | < 5 minutes |
| Thumbnail cache check | < 1ms per file |

### Required Dependencies for Scale

Add these to `pyproject.toml` for 10M file support:
```toml
[project.dependencies]
cachetools = ">=5.0"          # TTL/LRU caches
pybloom-live = ">=3.0"        # Bloom filters
psycopg2-binary = ">=2.9"     # PostgreSQL (recommended for 10M+)
```

---

## Development Commands

### Environment Setup
```bash
# Requires Python 3.12 (NOT 3.13)
pip install -e ".[dev]"  # Install editable package with dev dependencies

# Optional: Install pre-commit hooks
pre-commit install
```

### Running the Application
```bash
# Run TagStudio GUI
python -m tagstudio.main

# Run with specific library
python -m tagstudio.main --open /path/to/library

# Run with PostgreSQL (recommended for 10M+ files)
python -m tagstudio.main --db-url "postgresql://user:pass@localhost/tagstudio"

# Batch thumbnail generation (optimized for 10M files)
python -m tagstudio.core.utils.thumbnail_generator /path/to/library --workers 8 --batch-size 5000
```

### Testing
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_library.py

# Run with coverage
pytest tests/ --cov

# Run scale tests (10M file simulation)
pytest tests/test_scale.py -v
```

### Code Quality Checks
```bash
# Linting (runs in CI)
ruff check

# Auto-fix linting issues
ruff check --fix

# Formatting
ruff format

# Type checking (runs in CI)
mypy --config-file pyproject.toml .

# First-time mypy setup:
mkdir -p .mypy_cache
mypy --install-types --non-interactive
```

---

## Architecture

### Database Layer (`src/tagstudio/core/library/alchemy/`)

**SQLAlchemy-based ORM** managing metadata storage:

- **`models.py`**: Core database models
  - `Entry`: Represents a file in the library (linked to filesystem path)
  - `Tag`: Tag definitions with name, color, aliases, parent tags
  - `Folder`: Library folder paths with UUIDs
  - `TagColorGroup`: Color scheme definitions for tags
  - Field models: `TextField`, `DatetimeField`, `BooleanField` (EAV pattern)
  - **⚠️ All models MUST have appropriate indexes for 10M scale**

- **`library.py`**: Main Library class - central hub for all database operations
  - Opening/creating libraries
  - Entry management (add, update, search)
  - Tag operations (create, update, delete, parent relationships)
  - Search query execution (see Query Language below)
  - Migration from legacy JSON format
  - **⚠️ All queries MUST use pagination or streaming**

- **`fields.py`**: Field system for flexible metadata (EAV-like pattern)
  - `BaseField`, `TextField`, `DatetimeField`, `BooleanField`
  - Fields store metadata values linked to entries

### Query Language (`src/tagstudio/core/query_lang/`)

Custom search language with boolean operators:
- **`tokenizer.py`**: Lexical analysis
- **`parser.py`**: Builds AST from tokens
- **`ast.py`**: AST node definitions
- **`visitors.py`**: SQL generation from AST (`SQLBoolExpressionBuilder`)

**Query examples:**
- `tag:nature AND tag:landscape`
- `path:*.jpg OR path:*.png`
- `mediatype:video NOT tag:processed`
- `special:untagged` (files without tags)

**⚠️ Search queries MUST:**
- Use database indexes (no sequential scans)
- Return paginated results (never all 10M)
- Cache results with TTL
- Execute in background threads

### Thumbnail System

**Two-tier caching architecture:**

1. **Runtime Rendering** (`src/tagstudio/qt/widgets/thumb_renderer.py`):
   - On-demand thumbnail generation for UI display
   - Supports images, videos, PDFs, fonts, audio, 3D models, office docs
   - **Animated thumbnails**: Videos and GIF/WebP animations
   - **EXR sequences**: Special support for image sequences with animated playback
   - **⚠️ MUST use request batching and async generation**

2. **Batch Generation** (`src/tagstudio/core/utils/thumbnail_generator.py`):
   - Optimized CLI tool for pre-generating thumbnails
   - **Multiprocessing with 8+ workers for 10M files**
   - Priority queuing (videos > sequences > images)
   - Progress tracking with SQLite database
   - **⚠️ MUST process in batches of 5,000+**

**Cache Storage** (`CacheManager`):
- Location: `<library>/.TagStudio/thumbs/`
- **Hash-based filenames**: `hash_128(filepath + mtime_ns)[0:8]`
  - Static: `.TagStudio/thumbs/<hash_prefix>/<hash>.webp`
  - Animated: `.TagStudio/thumbs/animated_<hash>.webp`
- **No database references**: Thumbnails discovered by hash lookup
- Auto-invalidation on file modification (mtime change)
- **Size-based LRU eviction (2GB limit for 10M files)**
- **⚠️ MUST use bloom filter for existence checks**

### Qt UI Layer (`src/tagstudio/qt/`)

**MVC-style architecture** with PySide6:

- **`ts_qt.py`**: `QtDriver` - Main Qt application driver and controller
  - Coordinates between UI, library, and core logic
  - Manages threading for long-running operations
  - Signal/slot event handling
  - **⚠️ ALL database operations MUST be in background threads**

- **`main_window.py`**: Main application window UI

- **Widgets** (`src/tagstudio/qt/widgets/`):
  - `item_thumb.py`: Thumbnail grid items
  - `preview_panel_view.py`: File preview sidebar
  - `fields.py`: Metadata field editors
  - `media_player.py`: Audio/video playback
  - **⚠️ ALL list views MUST use virtual scrolling**

- **Modals** (`src/tagstudio/qt/modals/`): Dialog boxes for operations
  - `build_tag.py`: Tag creation/editing
  - `tag_database.py`: Tag manager
  - `fix_unlinked.py`: Relink moved files

### Core Services (`src/tagstudio/core/`)

- **`ts_core.py`**: `TagStudioCore` - Business logic coordinator (legacy, being phased out)
- **`driver.py`**: `DriverMixin` - Common driver interface
- **`global_settings.py`**: User preferences (theme, window state, etc.)
- **Utils**:
  - `refresh_dir.py`: Directory scanning for new/modified files
    - **⚠️ MUST use parallel directory walker**
  - `missing_files.py`: Detecting unlinked entries
    - **⚠️ MUST stream results, not load all**
  - `is_sequences.py`: EXR image sequence detection and rendering

### Special File Formats

**EXR Sequences** (`src/tagstudio/core/utils/is_sequences.py`):
- Detects numbered EXR sequences (e.g., `render.0001.exr`, `render.0002.exr`)
- Creates "poster frame" entry (is_sequence=False) representing the sequence
- Other frames marked with is_sequence=True (hidden from UI)
- Generates animated thumbnails from sequence frames
- HDR tone mapping for proper display

---

## Code Style Guidelines

### Import Conventions
- **Do NOT** prepend local imports with `tagstudio`, use relative imports from `src/`
- Use `from pathlib import Path` instead of `os.path`
- Use `platform.system()` instead of `os.name`/`sys.platform`

### Logging
```python
import structlog
logger = structlog.get_logger(__name__)

# Use logger instead of print()
logger.info("Processing entries", count=100)
logger.error("Failed to load", filepath=path, error=e)

# Log progress for long operations (10M files)
if processed % 100000 == 0:
    logger.info("Progress", processed=processed, total=total)
```

### Database Operations
```python
# Always use context managers for sessions
from sqlalchemy.orm import Session

with Session(library.engine) as session:
    # ⚠️ NEVER use .all() for large result sets
    # ✅ Use yield_per for streaming
    for entry in session.query(Entry).yield_per(1000):
        process(entry)
    session.commit()

# ✅ Batch operations
session.bulk_insert_mappings(Entry, entries_chunk)
session.bulk_update_mappings(Entry, updates_chunk)
```

### Qt Patterns
- Use HTML-like tags in Qt widgets over stylesheets when possible
- **Prefer signals/slots for async operations - NEVER block UI thread**
- Use `QThreadPool` + `CustomRunnable` for background tasks
- **Use virtual models for lists - NEVER create 10M widgets**

### Avoid
- Nested f-strings
- Direct `os` module calls (use `pathlib`)
- Modifying legacy JSON library code (`src/core/library/json/`)
- Superfluous logging in production code
- **Loading all entries into memory**
- **Sequential processing of large datasets**
- **Blocking UI thread with database operations**
- **Creating widgets for all items (use virtual scrolling)**

---

## Testing

- Tests located in `tests/` directory
- Qt-specific tests in `tests/qt/`
- Test fixtures in `tests/fixtures/`
- Use `pytest-qt` for Qt widget testing
- Snapshots with `syrupy` for regression testing

### Scale Testing Requirements

**All new features MUST include scale tests:**

```python
# tests/test_scale.py
import pytest

@pytest.mark.scale
def test_search_performance_10m(library_10m_fixture):
    """Search must complete in < 3 seconds for 10M entries."""
    import time
    start = time.time()
    
    results = library_10m_fixture.search_entries("tag:nature", limit=100)
    
    elapsed = time.time() - start
    assert elapsed < 3.0, f"Search took {elapsed:.2f}s, expected < 3s"
    assert len(results) <= 100

@pytest.mark.scale
def test_batch_insert_performance(library_fixture):
    """Batch insert 100K entries in < 10 seconds."""
    entries = [{'path': f'/test/{i}.jpg'} for i in range(100000)]
    
    start = time.time()
    library_fixture.add_entries_batch(entries)
    elapsed = time.time() - start
    
    assert elapsed < 10.0, f"Insert took {elapsed:.2f}s, expected < 10s"

@pytest.mark.scale
def test_memory_usage_streaming(library_10m_fixture):
    """Memory must stay < 500MB when streaming 10M entries."""
    import tracemalloc
    tracemalloc.start()
    
    count = 0
    for entry in library_10m_fixture.iter_entries():
        count += 1
        if count % 1000000 == 0:
            current, peak = tracemalloc.get_traced_memory()
            assert peak < 500 * 1024 * 1024, f"Peak memory {peak/1024/1024:.0f}MB > 500MB"
    
    tracemalloc.stop()
```

---

## Migration Notes

**JSON → SQLAlchemy Migration** (v9.5):
- Legacy JSON libraries auto-migrate on first open
- Migration code: `src/tagstudio/core/library/alchemy/library.py` (search for "migrate")
- Backups created automatically in `.TagStudio/backups/`
- Old JSON code remains in `src/core/library/json/` (DO NOT MODIFY)
- **⚠️ Migration MUST use batch operations for 10M file libraries**

---

## Common Patterns

### Adding a New Field Type
1. Add enum to `FieldTypeEnum` in `enums.py`
2. Create field class in `fields.py` inheriting from `BaseField`
3. Add database model in `models.py` **with appropriate indexes**
4. Update field factory in `library.py`
5. Create Qt widget in `qt/widgets/fields.py`

### Adding Search Operators
1. Add token type in `query_lang/tokenizer.py`
2. Update parser in `query_lang/parser.py`
3. Implement SQL visitor in `query_lang/visitors.py`
4. **⚠️ Ensure generated SQL uses indexes**

### Adding Thumbnail Support
1. Add media type to `core/media_types.py`
2. Implement renderer in `qt/widgets/thumb_renderer.py` (e.g., `_<format>_thumb()`)
3. Add to batch generator in `core/utils/thumbnail_generator.py` if needed
4. **⚠️ Use async generation with request batching**

### Adding a New Database Query
1. **Add appropriate indexes to models.py**
2. **Use streaming (yield_per) or pagination**
3. **Add query result caching if frequently called**
4. **Execute in background thread if called from UI**
5. **Write scale test for 10M entries**

---

## Dependencies

- **PySide6 6.8.0**: Qt bindings (exact version pinned)
- **SQLAlchemy 2.0**: Database ORM
- **Pillow**: Image processing (with HEIF, JXL plugins)
- **OpenCV**: Video frame extraction
- **FFmpeg**: Required external dependency for video support
- **PostgreSQL support**: psycopg2-binary for Postgres databases **(recommended for 10M+)**
- **cachetools**: TTL/LRU caching for 10M scale
- **pybloom-live**: Bloom filters for fast existence checks

---

## Configuration for 10M Files

### Recommended Settings

```python
# src/tagstudio/core/global_settings.py

class GlobalSettings:
    # Database
    DB_BATCH_SIZE: int = 10000
    DB_CONNECTION_POOL_SIZE: int = 20
    DB_QUERY_TIMEOUT_MS: int = 300000
    
    # UI
    UI_PAGE_SIZE: int = 100
    UI_VIRTUAL_SCROLL_BUFFER: int = 50
    
    # Thumbnails
    THUMB_WORKERS: int = 8
    THUMB_BATCH_SIZE: int = 5000
    THUMB_CACHE_SIZE_MB: int = 2000
    
    # Memory
    ENTRY_CACHE_SIZE: int = 50000
    TAG_CACHE_SIZE: int = 10000
    SEARCH_CACHE_SIZE: int = 100
```

### PostgreSQL Configuration (Recommended for 10M+)

```ini
# postgresql.conf
shared_buffers = 4GB
effective_cache_size = 12GB
work_mem = 256MB
maintenance_work_mem = 1GB
random_page_cost = 1.1  # SSD
max_parallel_workers_per_gather = 4
```

### SQLite Configuration

```python
# Applied automatically when using SQLite
PRAGMA journal_mode=WAL;
PRAGMA cache_size=-500000;  -- 500MB
PRAGMA mmap_size=2147483648;  -- 2GB
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
```

---

## File Locations

**Library Structure:**
```
<library-root>/
├── .TagStudio/
│   ├── ts_library.db          # SQLite database
│   ├── thumbs/                # Thumbnail cache (2GB limit)
│   │   ├── <timestamp>/       # Cache folders
│   │   │   ├── ab/            # Hash prefix folders
│   │   │   │   └── abc123.webp
│   │   └── animated_abc123.webp
│   ├── backups/               # Library backups
│   └── collages/              # Generated collages
└── [user files...]            # 10,000,000+ files
```

**Global Settings:**
- Windows: `%APPDATA%/TagStudio/settings.toml`
- macOS: `~/Library/Application Support/TagStudio/settings.toml`
- Linux: `~/.config/TagStudio/settings.toml`

---

## Summary: 10M File Checklist

Before submitting code, verify:

- [ ] No `.all()` calls on Entry queries (use `yield_per` or pagination)
- [ ] Batch operations for inserts/updates (10,000+ per batch)
- [ ] Database indexes on all filtered/sorted columns
- [ ] Background threads for all database operations in UI
- [ ] Virtual scrolling for all list views
- [ ] Caching for frequently accessed data
- [ ] Parallel processing for batch operations
- [ ] Progress logging for operations > 1 second
- [ ] Scale tests included for new features
- [ ] Memory usage < 500MB when processing all entries