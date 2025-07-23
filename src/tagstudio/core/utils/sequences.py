import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tagstudio.core.library.alchemy.library import Library
    from tagstudio.core.library.alchemy.models import Entry
    from tagstudio.core.library.alchemy.enums import BrowsingState

SEQUENCE_RE = re.compile(r"^(.*?)[._-](\d{3,6})$")


@dataclass
class SequenceRegistry:
    """Lazy-loading sequence registry optimized for large libraries."""
    
    library: "Library"
    _sequence_cache: dict[int, list[int]] = field(default_factory=dict)
    _cache_max_size: int = field(default=10000)
    _cache_access_order: list[int] = field(default_factory=list)
    _progressive_cache: dict[int, tuple[list["Entry"], list[int | None]]] = field(default_factory=dict)
    
    def get_complete_sequence(self, entry: "Entry") -> list["Entry"]:
        """Get sequence siblings via cached SQL query."""
        try:
            if entry.id in self._sequence_cache:
                # Move to end for LRU tracking
                if entry.id in self._cache_access_order:
                    self._cache_access_order.remove(entry.id)
                self._cache_access_order.append(entry.id)
                
                # Filter out None entries in case of database issues
                cached_entries = [self.library.get_entry(eid) for eid in self._sequence_cache[entry.id]]
                return [e for e in cached_entries if e is not None]
            
            match = SEQUENCE_RE.match(entry.path.stem)
            if not match:
                self._add_to_cache([entry.id])
                return [entry]
            
            # SQL query only for this specific sequence
            base_name = match.group(1)
            sequence_entries = self._query_sequence_siblings(entry, base_name)
            
            if not sequence_entries:
                # Fall back to single entry if query fails
                self._add_to_cache([entry.id])
                return [entry]
            
            # Cache all entries in this sequence
            entry_ids = [e.id for e in sequence_entries]
            self._add_to_cache(entry_ids)
                
            return sequence_entries
        except Exception:
            # Fall back to single entry on any error
            return [entry]
    
    def get_sequence_aware_page(self, page_num: int, page_size: int, 
                              browsing_state: "BrowsingState | None" = None) -> tuple[list["Entry"], list[int | None], int]:
        """Get page with proper sequence grouping using smart streaming approach."""
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        # For pagination, we need to estimate total count efficiently
        if browsing_state and (getattr(browsing_state, 'query', None) or getattr(browsing_state, 'ast', None)):
            # For filtered results, get all (usually small result set)
            all_results = self.library.search_library(browsing_state, 999999)
            candidate_entries = all_results.items
            # Process all for accurate count since result set is filtered and smaller
            all_items, all_counts = self._process_entries_for_sequences(candidate_entries)
            total_count = len(all_items)
            start_idx = page_num * page_size
            end_idx = start_idx + page_size
            return all_items[start_idx:end_idx], all_counts[start_idx:end_idx], total_count
        else:
            # For unfiltered results, use streaming approach for large libraries
            return self._get_page_streaming(page_num, page_size)
    
    def _get_all_grouped_items(self, browsing_state: "BrowsingState | None" = None) -> tuple[list["Entry"], list[int | None]]:
        """Get all items with sequence grouping applied."""
        # Check if we have this cached
        cache_key = str(browsing_state) if browsing_state else "all"
        if hasattr(self, '_grouped_cache') and cache_key in self._grouped_cache:
            return self._grouped_cache[cache_key]
        
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        # Get all entries that match the search/filter
        if browsing_state and (getattr(browsing_state, 'query', None) or getattr(browsing_state, 'ast', None)):
            # Get all filtered results (not just a page)
            all_results = self.library.search_library(browsing_state, 999999)  # Large number to get all
            candidate_entries = all_results.items
        else:
            # Get all entries
            candidate_entries = self._get_all_entries()
        
        # Apply sequence grouping to all entries
        for entry in candidate_entries:
            if entry.id in processed_ids:
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
        
        # Cache the result (with size limit)
        if not hasattr(self, '_grouped_cache'):
            self._grouped_cache = {}
        
        # Keep cache size reasonable
        if len(self._grouped_cache) > 5:
            self._grouped_cache.clear()
        
        self._grouped_cache[cache_key] = (display_items, frame_counts)
        return display_items, frame_counts
    
    def _get_page_streaming(self, page_num: int, page_size: int) -> tuple[list["Entry"], list[int | None], int]:
        """High-performance streaming approach for large unfiltered libraries."""
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        # Estimate how many entries we need to process to get a full page
        # Assume average 20% are sequences, so we need ~1.25x entries to get page_size items
        estimated_multiplier = 1.5
        batch_size = max(page_size * 2, 200)  # Minimum reasonable batch
        
        offset = 0
        target_items = page_size
        skipped_due_to_sequences = 0
        
        while len(display_items) < target_items:
            # Get a batch of entries
            batch = self._get_entries_batch(offset, batch_size)
            if not batch:
                break
                
            batch_start_count = len(display_items)
            
            for entry in batch:
                if entry.id in processed_ids:
                    continue
                    
                # Quick check: does this look like a sequence?
                if SEQUENCE_RE.match(entry.path.stem):
                    # It's a potential sequence - get the full sequence
                    sequence_entries = self.get_complete_sequence(entry)
                    if len(sequence_entries) > 1:
                        # It's a sequence - use the poster
                        poster = min(sequence_entries, key=lambda e: e.path)
                        if poster.id not in processed_ids:
                            display_items.append(poster)
                            frame_counts.append(len(sequence_entries))
                            processed_ids.update(e.id for e in sequence_entries)
                        continue
                
                # It's a single file
                if entry.id not in processed_ids:
                    display_items.append(entry)
                    frame_counts.append(None)
                    processed_ids.add(entry.id)
                
                if len(display_items) >= target_items:
                    break
            
            # If we didn't make progress, increase batch size or break
            if len(display_items) == batch_start_count:
                if batch_size < 1000:
                    batch_size *= 2
                else:
                    break
            
            offset += len(batch)
            
            # Safety valve for very large libraries
            if offset > 50000:  # Processed 50k entries, estimate total
                break
        
        # Estimate total count based on what we've seen
        if offset > 0:
            ratio = len(display_items) / offset  # display items per raw entry
            total_entries = self.library.entries_count
            estimated_total = int(total_entries * ratio)
        else:
            estimated_total = len(display_items)
        
        # Cache this result for future use
        self._progressive_cache[0] = (display_items[:page_size], frame_counts[:page_size])
        
        # Apply pagination offset
        start_idx = page_num * page_size
        if page_num > 0:
            # For subsequent pages, try to use progressive approach
            return self._get_page_progressive(page_num, page_size, estimated_total)
        
        return display_items[:page_size], frame_counts[:page_size], estimated_total
    
    def _get_exact_page(self, page_num: int, page_size: int) -> tuple[list["Entry"], list[int | None], int]:
        """Get exact page for page_num > 0 using smart streaming."""
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        # Calculate how many display items we need to skip
        target_skip = page_num * page_size
        target_items = page_size
        
        # Stream through entries until we reach the target page
        offset = 0
        batch_size = 500
        items_found = 0
        
        while items_found < target_skip + target_items:
            batch = self._get_entries_batch(offset, batch_size)
            if not batch:
                break
                
            for entry in batch:
                if entry.id in processed_ids:
                    continue
                
                # Process this entry (sequence or single)
                if SEQUENCE_RE.match(entry.path.stem):
                    sequence_entries = self.get_complete_sequence(entry)
                    if len(sequence_entries) > 1:
                        poster = min(sequence_entries, key=lambda e: e.path)
                        if poster.id not in processed_ids:
                            if items_found >= target_skip:
                                display_items.append(poster)
                                frame_counts.append(len(sequence_entries))
                            items_found += 1
                            processed_ids.update(e.id for e in sequence_entries)
                        continue
                
                # Single file
                if entry.id not in processed_ids:
                    if items_found >= target_skip:
                        display_items.append(entry)
                        frame_counts.append(None)
                    items_found += 1
                    processed_ids.add(entry.id)
                
                if len(display_items) >= target_items:
                    break
            
            offset += len(batch)
            
            # Safety valve
            if offset > 100000:
                break
        
        # Estimate total based on what we've processed
        if offset > 0:
            ratio = items_found / offset
            estimated_total = int(self.library.entries_count * ratio)
        else:
            estimated_total = items_found
        
        return display_items[:target_items], frame_counts[:target_items], estimated_total
    
    def _get_page_progressive(self, page_num: int, page_size: int, estimated_total: int) -> tuple[list["Entry"], list[int | None], int]:
        """Get page using progressive caching for better performance."""
        # Check if we have this page cached
        if page_num in self._progressive_cache:
            cached_items, cached_counts = self._progressive_cache[page_num]
            return cached_items, cached_counts, estimated_total
        
        # Find the highest cached page before this one
        max_cached_page = -1
        for cached_page in self._progressive_cache.keys():
            if cached_page < page_num:
                max_cached_page = max(max_cached_page, cached_page)
        
        if max_cached_page >= 0:
            # Start from the last cached page
            start_items_to_skip = (max_cached_page + 1) * page_size
        else:
            # No cache, start from beginning
            start_items_to_skip = 0
        
        # Use the exact page method but with optimized starting point
        return self._get_exact_page(page_num, page_size)
    
    def _process_entries_for_sequences(self, entries: list["Entry"]) -> tuple[list["Entry"], list[int | None]]:
        """Process a list of entries and group sequences."""
        display_items: list["Entry"] = []
        frame_counts: list[int | None] = []
        processed_ids: set[int] = set()
        
        for entry in entries:
            if entry.id in processed_ids:
                continue
                
            sequence_entries = self.get_complete_sequence(entry)
            
            if len(sequence_entries) > 1:
                poster = min(sequence_entries, key=lambda e: e.path)
                display_items.append(poster)
                frame_counts.append(len(sequence_entries))
                processed_ids.update(e.id for e in sequence_entries)
            else:
                display_items.append(entry)
                frame_counts.append(None)
                processed_ids.add(entry.id)
        
        return display_items, frame_counts
    
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
        try:
            from sqlalchemy.orm import Session
            from tagstudio.core.library.alchemy.models import Entry as EntryModel
            
            # Use GLOB pattern for efficient matching
            parent_path = str(entry.path.parent)
            if parent_path == ".":
                parent_path = ""
            
            pattern = f"{parent_path}/{base_name}[._-][0-9][0-9][0-9]*" if parent_path else f"{base_name}[._-][0-9][0-9][0-9]*"
            
            # Query using the library's Entry model directly
            with Session(self.library.engine) as session:
                entries = (
                    session.query(EntryModel)
                    .filter(EntryModel.path.op('GLOB')(pattern))
                    .order_by(EntryModel.path)
                    .all()
                )
            
            return entries
        except Exception:
            # Return empty list on database error
            return []
    
    def _get_entries_batch(self, offset: int, limit: int) -> list["Entry"]:
        """Get entries batch with optimized query."""
        try:
            from sqlalchemy.orm import Session, selectinload
            from tagstudio.core.library.alchemy.models import Entry as EntryModel
            
            with Session(self.library.engine) as session:
                return (
                    session.query(EntryModel)
                    .options(selectinload(EntryModel.tags))
                    .order_by(EntryModel.id)
                    .offset(offset)
                    .limit(limit)
                    .all()
                )
        except Exception:
            # Return empty list on database error
            return []
    
    def _get_all_entries(self) -> list["Entry"]:
        """Get all entries from the library."""
        try:
            from sqlalchemy.orm import Session, selectinload
            from tagstudio.core.library.alchemy.models import Entry as EntryModel
            
            with Session(self.library.engine) as session:
                return (
                    session.query(EntryModel)
                    .options(selectinload(EntryModel.tags))
                    .order_by(EntryModel.id)
                    .all()
                )
        except Exception:
            # Return empty list on database error
            return []
    
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
        self._progressive_cache.clear()
        if hasattr(self, '_grouped_cache'):
            self._grouped_cache.clear()