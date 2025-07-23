import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tagstudio.core.library.alchemy.library import Library
    from tagstudio.core.library.alchemy.models import Entry

SEQUENCE_RE = re.compile(r"^(.*?)[._-](\d{3,6})$")


@dataclass
class SequenceRegistry:
    """Lazy-loading sequence registry optimized for large libraries."""
    
    library: "Library"
    _sequence_cache: dict[int, list[int]] = field(default_factory=dict)
    _cache_max_size: int = field(default=10000)
    _cache_access_order: list[int] = field(default_factory=list)
    
    def get_complete_sequence(self, entry: "Entry") -> list["Entry"]:
        """Get sequence siblings via cached SQL query."""
        if entry.id in self._sequence_cache:
            # Move to end for LRU tracking
            if entry.id in self._cache_access_order:
                self._cache_access_order.remove(entry.id)
            self._cache_access_order.append(entry.id)
            
            return [self.library.get_entry(eid) for eid in self._sequence_cache[entry.id]]
        
        match = SEQUENCE_RE.match(entry.path.stem)
        if not match:
            self._add_to_cache([entry.id])
            return [entry]
        
        # SQL query only for this specific sequence
        base_name = match.group(1)
        sequence_entries = self._query_sequence_siblings(entry, base_name)
        
        # Cache all entries in this sequence
        entry_ids = [e.id for e in sequence_entries]
        self._add_to_cache(entry_ids)
            
        return sequence_entries
    
    def get_sequence_aware_page(self, page_num: int, page_size: int, 
                              browsing_state: "BrowsingState | None" = None) -> tuple[list["Entry"], list[int | None]]:
        """Get page with proper sequence grouping and search/filter support."""
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        # Use existing search/filter if provided
        if browsing_state and (getattr(browsing_state, 'query', None) or getattr(browsing_state, 'tag_ids', None)):
            # Get filtered results first, then apply sequence grouping
            results = self.library.get_browsing_results(browsing_state, page_size=page_size * 3)
            candidate_entries = results.items
        else:
            # Get all entries for sequence processing
            db_offset = page_num * page_size
            candidate_entries = self._get_entries_batch(db_offset, page_size * 2)
        
        # Apply sequence grouping to candidates
        for entry in candidate_entries:
            if entry.id in processed_ids or len(display_items) >= page_size:
                continue
                
            sequence_entries = self.get_complete_sequence(entry)
            
            if len(sequence_entries) > 1:
                # Use first frame as poster
                poster = min(sequence_entries, key=lambda e: e.path)
                display_items.append(poster)
                frame_counts.append(len(sequence_entries))
                processed_ids.update(e.id for e in sequence_entries)
            else:
                display_items.append(entry)
                frame_counts.append(None)
                processed_ids.add(entry.id)
        
        return display_items[:page_size], frame_counts[:page_size]
    
    def ids_for_poster(self, entry_id: int) -> list[int]:
        """Return all entry IDs for the sequence represented by entry_id."""
        if entry_id in self._sequence_cache:
            return self._sequence_cache[entry_id].copy()
        
        # Load sequence on demand
        entry = self.library.get_entry(entry_id)
        if entry:
            sequence_entries = self.get_complete_sequence(entry)
            return [e.id for e in sequence_entries]
        
        return [entry_id]
    
    def get_total_display_count(self) -> int:
        """Estimate total display items after sequence grouping."""
        total_entries = self.library.entries_count
        # Assume average 20% reduction due to sequence grouping
        return int(total_entries * 0.8)
    
    def _query_sequence_siblings(self, entry: "Entry", base_name: str) -> list["Entry"]:
        """Optimized SQL query for specific sequence siblings."""
        from tagstudio.core.library.alchemy.models import Entry as EntryModel
        
        # Use GLOB pattern for efficient matching
        parent_path = str(entry.path.parent)
        if parent_path == ".":
            parent_path = ""
        
        pattern = f"{parent_path}/{base_name}[._-][0-9][0-9][0-9]*" if parent_path else f"{base_name}[._-][0-9][0-9][0-9]*"
        
        # Query using the library's Entry model directly
        entries = (
            self.library.session.query(EntryModel)
            .filter(EntryModel.path.op('GLOB')(pattern))
            .order_by(EntryModel.path)
            .all()
        )
        
        return entries
    
    def _get_entries_batch(self, offset: int, limit: int) -> list["Entry"]:
        """Get entries batch with optimized query."""
        from sqlalchemy.orm import selectinload
        from tagstudio.core.library.alchemy.models import Entry as EntryModel
        
        return (
            self.library.session.query(EntryModel)
            .options(selectinload(EntryModel.tags))
            .order_by(EntryModel.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    
    def _add_to_cache(self, entry_ids: list[int]) -> None:
        """Add entries to cache with LRU eviction."""
        # Evict old entries if cache is full
        if len(self._sequence_cache) + len(entry_ids) > self._cache_max_size:
            evict_count = len(entry_ids)
            for _ in range(evict_count):
                if self._cache_access_order:
                    old_id = self._cache_access_order.pop(0)
                    self._sequence_cache.pop(old_id, None)
        
        # Add new entries
        for eid in entry_ids:
            self._sequence_cache[eid] = entry_ids.copy()
            if eid not in self._cache_access_order:
                self._cache_access_order.append(eid)
    
    def clear_cache(self) -> None:
        """Clear all cached sequences."""
        self._sequence_cache.clear()
        self._cache_access_order.clear()