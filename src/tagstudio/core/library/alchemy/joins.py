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


class TagEntry(Base):
    __tablename__ = "tag_entries"

    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), primary_key=True)

    # Priority 1 index for reverse tag lookups
    # Note: Composite PK (tag_id, entry_id) already indexes tag_id queries
    # This index optimizes entry_id queries: "what tags does entry X have?"
    __table_args__ = (
        Index('ix_tag_entries_entry_id', 'entry_id'),
    )
