import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Entry

SEQUENCE_RE = re.compile(r"^(.*?)[._-](\d{3,6})$")


@dataclass
class SequenceEntry:
    """Container for a group of sequential frame entries."""

    entries: list[Entry] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.entries)

    @property
    def poster(self) -> Entry | None:
        return self.entries[0] if self.entries else None


@dataclass
class SequenceRegistry:
    """Detect and store image sequences from a Library."""

    library: Library
    sequences: list[SequenceEntry] = field(default_factory=list)
    entry_to_sequence: dict[int, SequenceEntry] = field(default_factory=dict)

    @property
    def sequences_count(self) -> int:
        return len(self.sequences)

    def refresh_sequences(self) -> Iterator[int]:
        """Detect groups of files that appear to be part of a sequence."""
        groups: dict[tuple[Path, str, str], SequenceEntry] = defaultdict(SequenceEntry)

        for i, entry in enumerate(self.library.get_entries()):
            match = SEQUENCE_RE.match(entry.path.stem)
            if match:
                base = match.group(1)
                key = (entry.path.parent, base, entry.suffix)
                groups[key].entries.append(entry)
            yield i

        # Keep only groups with more than one frame
        self.sequences = [
            SequenceEntry(sorted(seq.entries, key=lambda e: e.path))
            for seq in groups.values()
            if len(seq.entries) > 1
        ]
        self.entry_to_sequence.clear()
        for seq in self.sequences:
            for e in seq.entries:
                self.entry_to_sequence[e.id] = seq
                
    def ids_for_poster(self, entry_id: int) -> list[int]:
        """Return all entry IDs for the sequence represented by ``entry_id``.

        If ``entry_id`` is not recognised as the poster frame of a sequence,
        it is returned as-is.
        """
        seq = self.entry_to_sequence.get(entry_id)
        if seq and seq.poster and seq.poster.id == entry_id:
            return [e.id for e in seq.entries]
        return [entry_id]