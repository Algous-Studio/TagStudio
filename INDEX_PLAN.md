# TagStudio Database Index Plan

**Goal:** Optimize for 10,000,000+ file libraries

## Analysis Summary

### Current Query Patterns (from codebase analysis)

**Most Common Operations:**
1. **Entry lookups** - by folder, file type (suffix), sequence status
2. **Tag searches** - by name (with aliases), hierarchical queries
3. **Multi-tag filtering** - entries that have ALL of specific tags
4. **Sorting** - by date (created/modified/added), filename, path
5. **Field access** - get all fields for an entry by type

### Existing Indexes (Implicit)

✅ Already have (from PKs and unique constraints):
- `entries.id` (PK)
- `entries.path` (UNIQUE)
- `tags.id` (PK)
- `folders.id` (PK)
- `folders.path` (UNIQUE)
- `folders.uuid` (UNIQUE)
- `tag_entries(tag_id, entry_id)` (Composite PK)
- `tag_parents(parent_id, child_id)` (Composite PK)

---

## Proposed Indexes

### Priority 1: CRITICAL (Blocking 10M scale)

#### **ENTRIES Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_entries_folder_id` | `folder_id` | FK lookups, folder-based queries | `WHERE folder_id = X` |
| `ix_entries_suffix` | `suffix` | File type filtering (`filetype:jpg`) | `WHERE suffix IN ('jpg', 'png')` |
| `ix_entries_is_sequence` | `is_sequence` | Filter out sequence frames | `WHERE is_sequence = FALSE` |
| `ix_entries_folder_sequence` | `folder_id, is_sequence` | Common composite query | `WHERE folder_id = X AND is_sequence = FALSE` |

**Estimated Impact:** 30-60 second queries → < 1 second

#### **TAG_ENTRIES Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_tag_entries_entry_id` | `entry_id` | Reverse lookup: "what tags does entry X have?" | `WHERE entry_id = X` |
| `ix_tag_entries_tag_id` | `tag_id` | May help despite composite PK | `WHERE tag_id = X` (count queries) |

**Estimated Impact:** Tag filtering 10-30 seconds → < 1 second

**Note:** Composite PK `(tag_id, entry_id)` already creates an index that helps with `WHERE tag_id = X` queries, but may not be optimal for `WHERE entry_id = X` queries (depends on DB engine).

---

### Priority 2: HIGH (Performance bottlenecks)

#### **ENTRIES Table (Sorting)**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_entries_date_modified` | `date_modified` | Sort by modification date | `ORDER BY date_modified DESC` |
| `ix_entries_date_created` | `date_created` | Sort by creation date | `ORDER BY date_created DESC` |
| `ix_entries_date_added` | `date_added` | Sort by library add date | `ORDER BY date_added DESC` |
| `ix_entries_filename` | `filename` | Filename searches/sorts | `WHERE filename LIKE '%foo%'` |

**Estimated Impact:** 45-90 second sorts → < 2 seconds

#### **TAGS Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_tags_name` | `name` | Tag name searches | `WHERE name = 'landscape'` |
| `ix_tags_name_lower` | `LOWER(name)` | Case-insensitive sorting | `ORDER BY LOWER(name)` |

**Estimated Impact:** Tag searches/autocomplete 5-10 seconds → < 0.5 seconds

**Note:** Functional index `LOWER(name)` may not be supported in all databases. Alternative: Add a normalized `name_lower` column.

#### **TAG_ALIASES Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_tag_aliases_name` | `name` | Alias searches | `WHERE name = 'landscape'` |
| `ix_tag_aliases_tag_id` | `tag_id` | FK lookup (already exists?) | `WHERE tag_id = X` |

---

### Priority 3: MEDIUM (Field system optimization)

#### **TEXT_FIELDS Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_text_fields_entry_id` | `entry_id` | FK lookup: get all fields for entry | `WHERE entry_id = X` |
| `ix_text_fields_type_key` | `type_key` | FK lookup: filter by field type | `WHERE type_key = 'description'` |
| `ix_text_fields_entry_type` | `entry_id, type_key` | Composite: specific field for entry | `WHERE entry_id = X AND type_key = Y` |

#### **DATETIME_FIELDS Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_datetime_fields_entry_id` | `entry_id` | FK lookup | `WHERE entry_id = X` |
| `ix_datetime_fields_type_key` | `type_key` | FK lookup | `WHERE type_key = 'published_date'` |
| `ix_datetime_fields_entry_type` | `entry_id, type_key` | Composite lookup | `WHERE entry_id = X AND type_key = Y` |

#### **BOOLEAN_FIELDS Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_boolean_fields_entry_id` | `entry_id` | FK lookup | `WHERE entry_id = X` |
| `ix_boolean_fields_type_key` | `type_key` | FK lookup | `WHERE type_key = 'is_public'` |

---

### Priority 4: LOW (Edge cases & optimizations)

#### **TAGS Table (Advanced)**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_tags_is_category` | `is_category` | Filter category tags | `WHERE is_category = TRUE` |

#### **TAG_PARENTS Table**

| Index Name | Columns | Rationale | Query Pattern |
|-----------|---------|-----------|---------------|
| `ix_tag_parents_child_id` | `child_id` | Reverse hierarchy lookup | Recursive parent queries |

**Note:** Composite PK `(parent_id, child_id)` likely already optimizes `WHERE parent_id = X`.

---

## Index Size Estimates (for 10M files)

**Assumptions:**
- 10,000,000 entries
- Average 3 tags per entry → 30,000,000 tag_entries rows
- 10,000 tags
- Average 2 text fields per entry → 20,000,000 text_fields rows

| Table | Index | Est. Size | Notes |
|-------|-------|-----------|-------|
| entries | ix_entries_folder_id | ~40 MB | 10M × 4 bytes (int) |
| entries | ix_entries_suffix | ~100 MB | 10M × ~10 bytes (varchar) |
| entries | ix_entries_is_sequence | ~10 MB | 10M × 1 byte (bool) |
| entries | ix_entries_folder_sequence | ~50 MB | Composite |
| entries | ix_entries_date_* | ~80 MB each | 10M × 8 bytes (timestamp) |
| tag_entries | ix_tag_entries_entry_id | ~120 MB | 30M × 4 bytes |
| text_fields | ix_text_fields_entry_id | ~80 MB | 20M × 4 bytes |
| tags | ix_tags_name | ~1 MB | 10K × ~100 bytes |

**Total Additional Storage:** ~700 MB - 1 GB for all Priority 1-3 indexes

**Write Performance Impact:** +10-15% slower inserts (acceptable tradeoff)

**Read Performance Gain:** 10-100x faster queries

---

## Special Considerations

### 1. PostgreSQL vs SQLite

**PostgreSQL:**
- ✅ Supports partial indexes: `WHERE is_sequence = FALSE`
- ✅ Supports functional indexes: `LOWER(name)`
- ✅ Better parallel index builds
- ⚠️ Need `VACUUM ANALYZE` after bulk operations

**SQLite:**
- ❌ No partial indexes (full index required)
- ❌ No functional indexes (workaround: add column)
- ✅ Auto-manages with WAL mode
- ⚠️ Need `ANALYZE` after bulk operations

### 2. Composite Index Order Matters

```sql
-- ✅ GOOD: Can use for both queries
CREATE INDEX ix_entries_folder_sequence ON entries(folder_id, is_sequence);
-- Optimizes: WHERE folder_id = X AND is_sequence = FALSE
-- Also optimizes: WHERE folder_id = X (leading column)

-- ❌ BAD: Cannot use for WHERE folder_id = X
CREATE INDEX ix_entries_sequence_folder ON entries(is_sequence, folder_id);
-- Only optimizes: WHERE is_sequence = FALSE AND folder_id = X
```

### 3. Path Searches (GLOB/LIKE)

```sql
-- ❌ Index NOT used (wildcard at start)
WHERE path LIKE '%vacation%'

-- ✅ Index CAN be used (prefix match)
WHERE path LIKE 'photos/2024/%'

-- ⚠️ GLOB operator may not use index
WHERE path GLOB '*.jpg'  -- Full table scan
```

**Recommendation:** For full-text path searches at 10M scale, consider:
- PostgreSQL: Use `pg_trgm` extension with GIN index
- SQLite: Use FTS5 virtual table for path searches

---

## Implementation Strategy

### Phase 1: Critical Indexes (Priority 1)
1. Add to `models.py`
2. Create migration script
3. Test on 100K entry database
4. Deploy to users

**Expected Result:** Major performance improvement for folder/tag queries

### Phase 2: High Priority (Priority 2)
1. Add sorting indexes
2. Add tag name indexes
3. Test sorting performance

**Expected Result:** Fast sorting and tag autocomplete

### Phase 3: Field System (Priority 3)
1. Add field table indexes
2. Monitor impact on field queries

**Expected Result:** Faster metadata field access

### Phase 4: Monitoring & Optimization
1. Collect query statistics
2. Identify slow queries (EXPLAIN ANALYZE)
3. Add additional indexes as needed
4. Remove unused indexes

---

## Migration Considerations

### For Existing Databases

Users with existing databases need a migration:

```python
# Option 1: Alembic migration (preferred)
def upgrade():
    op.create_index('ix_entries_folder_id', 'entries', ['folder_id'])
    # ... etc

# Option 2: On-the-fly migration
def check_and_create_indexes(engine):
    """Create missing indexes on library open."""
    inspector = inspect(engine)
    existing = {idx['name'] for idx in inspector.get_indexes('entries')}

    if 'ix_entries_folder_id' not in existing:
        with engine.connect() as conn:
            conn.execute(text('CREATE INDEX ix_entries_folder_id ON entries(folder_id)'))
            conn.commit()
```

### Index Build Time

For 10M entries:
- **SQLite:** 5-15 minutes per index (single-threaded)
- **PostgreSQL:** 2-5 minutes per index (parallel builds)

**Show progress to user during migration!**

---

## Testing Plan

### 1. Create Test Database
```python
# Generate 10M test entries
for i in range(10_000_000):
    entries.append({'path': f'/test/{i}.jpg', 'folder_id': i % 1000, ...})
```

### 2. Benchmark Queries
```python
# Before indexes
start = time.time()
results = session.query(Entry).filter_by(folder_id=500).all()
print(f"Without index: {time.time() - start:.2f}s")

# After indexes
print(f"With index: {time.time() - start:.2f}s")
```

### 3. Monitor Index Usage
```sql
-- PostgreSQL: Check if indexes are being used
EXPLAIN ANALYZE SELECT * FROM entries WHERE folder_id = 500;

-- SQLite: Check query plan
EXPLAIN QUERY PLAN SELECT * FROM entries WHERE folder_id = 500;
```

---

## Questions to Answer

1. **Should we add ALL indexes at once, or phase them in?**
   - Recommendation: Start with Priority 1, then add others based on user feedback

2. **How do we handle the migration for existing users?**
   - Recommendation: Auto-migrate on app startup with progress dialog

3. **Should we support SQLite AND PostgreSQL optimally?**
   - Recommendation: Yes, but prioritize SQLite (more common for desktop app)

4. **What about partial indexes (PostgreSQL only)?**
   - Example: `CREATE INDEX ... WHERE is_sequence = FALSE`
   - Recommendation: Add as PostgreSQL-specific optimization

5. **Should we add FTS (Full-Text Search) for paths?**
   - Recommendation: Phase 2 feature, not critical for initial 10M support

---

## Summary: Recommended Immediate Action

**Add these 6 indexes first (Priority 1):**

1. ✅ `ix_entries_folder_id` on `entries(folder_id)`
2. ✅ `ix_entries_suffix` on `entries(suffix)`
3. ✅ `ix_entries_is_sequence` on `entries(is_sequence)`
4. ✅ `ix_entries_folder_sequence` on `entries(folder_id, is_sequence)`
5. ✅ `ix_tag_entries_entry_id` on `tag_entries(entry_id)`
6. ✅ `ix_tags_name` on `tags(name)`

**Estimated total impact:**
- Storage: +200 MB
- Write speed: -10%
- Read speed: +1000% (10-100x faster)
- Migration time: ~15-30 minutes for 10M entries

---

## Next Steps

1. **Review this plan** - Discuss with team/maintainers
2. **Start small** - Test Priority 1 indexes on 100K entry database
3. **Measure impact** - Benchmark before/after
4. **Iterate** - Add Priority 2/3 indexes based on real-world usage
5. **Document** - Add index rationale to CLAUDE.md
