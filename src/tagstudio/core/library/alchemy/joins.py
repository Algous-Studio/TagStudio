# Copyright (C) 2025
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio


from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from tagstudio.core.library.alchemy.db import Base


class TagParent(Base):
    __tablename__ = "tag_parents"

    parent_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)

    __table_args__ = (
        # Priority 4: Reverse hierarchy lookup (child -> parents)
        Index("ix_tag_parents_child_id", "child_id"),
    )


class TagEntry(Base):
    __tablename__ = "tag_entries"

    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), primary_key=True)

    __table_args__ = (
        # Priority 1 (CRITICAL): Reverse lookup - "what tags does entry X have?"
        # Note: Composite PK (tag_id, entry_id) already optimizes tag_id lookups
        Index("ix_tag_entries_entry_id", "entry_id"),
        # Priority 1: May help count queries despite composite PK
        Index("ix_tag_entries_tag_id", "tag_id"),
    )
