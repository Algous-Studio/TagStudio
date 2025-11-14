#!/usr/bin/env python3

"""
Optimized thumbnail generation service for TagStudio.
Designed to handle millions of files efficiently through batch processing,
smart caching, and background operation.

Usage:
    python -m tagstudio.core.utils.thumbnail_generator /path/to/library --workers 8 --batch-size 1000
"""

import argparse
import hashlib
import math
import multiprocessing as mp
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import structlog
from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import Session

from tagstudio.core.constants import THUMB_CACHE_NAME, TS_FOLDER_NAME
from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Entry
from tagstudio.core.media_types import MediaCategories
from tagstudio.core.utils.is_sequences import SequenceRegistry

logger = structlog.get_logger(__name__)


@dataclass
class ThumbnailJob:
    """Represents a thumbnail generation job."""
    entry_id: int
    file_path: Path
    file_type: str
    priority: int = 0
    file_size: int = 0
    mod_time: float = 0
    is_sequence: bool = False


@dataclass
class GenerationStats:
    """Statistics for thumbnail generation."""
    total_files: int = 0
    processed: int = 0
    skipped_existing: int = 0
    failed: int = 0
    animated_created: int = 0
    static_created: int = 0
    start_time: float = 0

    def __post_init__(self):
        if self.start_time == 0:
            self.start_time = time.time()

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    @property
    def files_per_second(self) -> float:
        elapsed = self.elapsed_time
        return self.processed / elapsed if elapsed > 0 else 0

    @property
    def eta_seconds(self) -> float:
        if self.files_per_second == 0 or self.processed == 0:
            return 0
        remaining = self.total_files - self.processed
        return remaining / self.files_per_second


class OptimizedThumbnailGenerator:
    """High-performance thumbnail generator for large libraries."""

    def __init__(
        self,
        library: Library,
        max_workers: int = None,
        batch_size: int = 1000,
        max_memory_mb: int = 2048
    ):
        self.library = library
        self.max_workers = max_workers or min(mp.cpu_count(), 8)
        self.batch_size = batch_size
        self.max_memory_mb = max_memory_mb

        # Cache directories
        self.cache_dir = library.library_dir / TS_FOLDER_NAME / THUMB_CACHE_NAME
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Animated cache in main directory for better performance
        self.animated_cache_dir = self.cache_dir

        # Progress tracking
        self.stats = GenerationStats()

        # Priority queues for different file types
        self.priority_queues = {
            'video': [],
            'sequence': [],
            'image': [],
            'other': []
        }

        # Thread-safe progress database
        self.progress_db_path = self.cache_dir / "generation_progress.db"
        self._init_progress_db()

        # Sequence registry (lazy loaded)
        self._sequence_registry = None

    def _init_progress_db(self):
        """Initialize SQLite database for tracking generation progress."""
        conn = sqlite3.connect(self.progress_db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS generation_progress (
                file_hash TEXT PRIMARY KEY,
                entry_id INTEGER,
                file_path TEXT,
                status TEXT,  -- 'pending', 'completed', 'failed', 'skipped'
                created_at REAL,
                completed_at REAL,
                error_message TEXT,
                thumbnail_type TEXT  -- 'static', 'animated', 'sequence'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON generation_progress(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entry_id ON generation_progress(entry_id)
        """)
        conn.commit()
        conn.close()

    def _get_sequence_registry(self) -> Optional[SequenceRegistry]:
        """Lazy-load sequence registry."""
        if self._sequence_registry is None:
            try:
                self._sequence_registry = SequenceRegistry(self.library)
                self._sequence_registry.refresh_sequences()
                logger.info(f"Loaded {len(self._sequence_registry.sequences)} sequences")
            except Exception as e:
                logger.error("Failed to load sequence registry", error=e)
        return self._sequence_registry

    def scan_library(self, force_regenerate: bool = False) -> None:
        """Scan library and build job queues."""
        logger.info("Scanning library for thumbnail generation jobs...")

        # Load sequence registry to handle EXR sequences
        sequence_registry = self._get_sequence_registry()

        with Session(self.library.engine) as session:
            # Get all entries efficiently
            entries = session.query(Entry).all()
            self.stats.total_files = len(entries)
            logger.info(f"Found {self.stats.total_files} files to process")

            # Load existing progress to avoid duplicates
            existing_hashes = self._get_existing_progress() if not force_regenerate else set()

            # Build prioritized job queues
            for entry in entries:
                file_path = self.library.library_dir / entry.path
                if not file_path.exists():
                    continue

                # Skip non-poster sequence frames (is_sequence=True means it's not a poster)
                if getattr(entry, 'is_sequence', False):
                    logger.debug(f"Skipping non-poster sequence frame: {file_path}")
                    self.stats.skipped_existing += 1
                    continue

                # Generate consistent hash
                file_hash = self._get_file_hash(file_path)

                if not force_regenerate and file_hash in existing_hashes:
                    self.stats.skipped_existing += 1
                    continue

                # Determine file type and priority
                file_type, priority = self._classify_file(entry, file_path)

                job = ThumbnailJob(
                    entry_id=entry.id,
                    file_path=file_path,
                    file_type=file_type,
                    priority=priority,
                    file_size=file_path.stat().st_size,
                    mod_time=file_path.stat().st_mtime,
                    is_sequence=getattr(entry, 'is_sequence', False)
                )

                self.priority_queues[file_type].append(job)

        # Sort queues by priority (higher priority first)
        for queue in self.priority_queues.values():
            queue.sort(key=lambda j: j.priority, reverse=True)

        total_jobs = sum(len(q) for q in self.priority_queues.values())
        logger.info(f"Queued {total_jobs} jobs ({self.stats.skipped_existing} skipped)")
        logger.info(f"Queue distribution: video={len(self.priority_queues['video'])}, "
                   f"sequence={len(self.priority_queues['sequence'])}, "
                   f"image={len(self.priority_queues['image'])}, "
                   f"other={len(self.priority_queues['other'])}")

    def _get_existing_progress(self) -> Set[str]:
        """Get set of file hashes that are already completed."""
        conn = sqlite3.connect(self.progress_db_path)
        cursor = conn.execute("SELECT file_hash FROM generation_progress WHERE status = 'completed'")
        hashes = {row[0] for row in cursor.fetchall()}
        conn.close()
        return hashes

    def _get_file_hash(self, filepath: Path) -> str:
        """Generate consistent hash for file (matches TagStudio's ThumbRenderer)."""
        mod_time = filepath.stat().st_mtime_ns if filepath.exists() else ""
        hashable_str = f"{str(filepath)}{mod_time}"
        return hashlib.shake_128(hashable_str.encode("utf-8")).hexdigest(8)

    def _classify_file(self, entry: Entry, file_path: Path) -> Tuple[str, int]:
        """Classify file type and assign priority."""
        ext = file_path.suffix.lower()

        # Priority scoring (0-100, higher = more important)
        priority = 50  # base priority

        # File size factor (smaller files first for quick wins)
        file_size = file_path.stat().st_size
        if file_size < 10 * 1024 * 1024:  # < 10MB
            priority += 20
        elif file_size > 1024 * 1024 * 1024:  # > 1GB
            priority -= 30

        # File type classification
        if MediaCategories.is_ext_in_category(ext, MediaCategories.VIDEO_TYPES):
            return 'video', priority + 30  # Videos highest priority (animated thumbs)

        # Check if this is a poster frame of a sequence
        sequence_registry = self._get_sequence_registry()
        if sequence_registry and sequence_registry.entry_to_sequence.get(entry.id):
            # This is a poster frame (is_sequence=False) of a sequence
            return 'sequence', priority + 25  # Sequences high priority

        if MediaCategories.is_ext_in_category(ext, MediaCategories.IMAGE_TYPES):
            if ext in {'.gif', '.webp', '.apng'}:
                priority += 15  # Animated images higher priority
            return 'image', priority + 10

        return 'other', priority

    def generate_all(
        self,
        force_regenerate: bool = False,
        file_types: Optional[List[str]] = None
    ) -> GenerationStats:
        """Generate thumbnails for all queued files."""
        self.scan_library(force_regenerate)

        # Filter by file types if specified
        if file_types:
            filtered_queues = {k: v for k, v in self.priority_queues.items() if k in file_types}
            self.priority_queues = filtered_queues

        # Process queues in priority order: video -> sequence -> image -> other
        queue_order = ['video', 'sequence', 'image', 'other']

        for queue_type in queue_order:
            if queue_type not in self.priority_queues:
                continue

            jobs = self.priority_queues[queue_type]
            if not jobs:
                continue

            logger.info(f"Processing {len(jobs)} {queue_type} files...")
            self._process_job_batches(jobs, queue_type)

        logger.info(f"Generation complete! Processed {self.stats.processed} files "
                   f"in {self.stats.elapsed_time:.1f}s "
                   f"({self.stats.files_per_second:.1f} files/sec)")

        return self.stats

    def _process_job_batches(self, jobs: List[ThumbnailJob], queue_type: str):
        """Process jobs in batches with multiprocessing."""
        total_jobs = len(jobs)

        for i in range(0, total_jobs, self.batch_size):
            batch = jobs[i:i + self.batch_size]
            batch_num = i // self.batch_size + 1
            total_batches = (total_jobs + self.batch_size - 1) // self.batch_size

            logger.info(f"Processing {queue_type} batch {batch_num}/{total_batches} "
                       f"({len(batch)} files)")

            # Use ProcessPoolExecutor for CPU-intensive work
            with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit batch jobs
                future_to_job = {
                    executor.submit(
                        process_thumbnail_job,
                        job,
                        str(self.cache_dir),
                        str(self.animated_cache_dir),
                        str(self.library.library_dir) if self.library.library_dir else None
                    ): job for job in batch
                }

                # Collect results
                for future in as_completed(future_to_job):
                    job = future_to_job[future]
                    try:
                        result = future.result(timeout=300)  # 5 min timeout per file
                        self._update_progress(job, result)
                        self.stats.processed += 1

                        if result.get('animated'):
                            self.stats.animated_created += 1
                        else:
                            self.stats.static_created += 1

                    except Exception as e:
                        logger.error("Job failed", job=job.file_path, error=e)
                        self._update_progress(job, {'status': 'failed', 'error': str(e)})
                        self.stats.failed += 1

            # Progress report
            progress_pct = (self.stats.processed / self.stats.total_files) * 100
            eta_min = self.stats.eta_seconds / 60
            logger.info(f"Progress: {progress_pct:.1f}% "
                       f"({self.stats.processed}/{self.stats.total_files}) "
                       f"ETA: {eta_min:.1f} min")

    def _update_progress(self, job: ThumbnailJob, result: Dict):
        """Update progress database with job result."""
        conn = sqlite3.connect(self.progress_db_path)
        file_hash = self._get_file_hash(job.file_path)

        conn.execute("""
            INSERT OR REPLACE INTO generation_progress
            (file_hash, entry_id, file_path, status, completed_at, error_message, thumbnail_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            file_hash,
            job.entry_id,
            str(job.file_path),
            result.get('status', 'completed'),
            time.time(),
            result.get('error'),
            'animated' if result.get('animated') else 'static'
        ))
        conn.commit()
        conn.close()


def process_thumbnail_job(job: ThumbnailJob, cache_dir: str, animated_cache_dir: str, library_dir: str = None) -> Dict:
    """
    Process a single thumbnail generation job.
    This function runs in a separate process for isolation and performance.
    """
    try:
        cache_path = Path(cache_dir)
        animated_path = Path(animated_cache_dir)

        # Generate cache filename (matches TagStudio's ThumbRenderer)
        mod_time = job.file_path.stat().st_mtime_ns if job.file_path.exists() else ""
        hashable_str = f"{str(job.file_path)}{mod_time}"
        file_hash = hashlib.shake_128(hashable_str.encode("utf-8")).hexdigest(8)

        # Check file type and generate appropriate thumbnail
        ext = job.file_path.suffix.lower()
        result = {'status': 'completed', 'animated': False}

        if MediaCategories.is_ext_in_category(ext, MediaCategories.VIDEO_TYPES):
            # Generate animated video thumbnail
            animated_cache_file = animated_path / f"animated_{file_hash}.webp"

            if not animated_cache_file.exists():
                frames = extract_video_frames(job.file_path)
                if len(frames) > 1:
                    save_animated_webp(frames, animated_cache_file)
                    result['animated'] = True

        elif job.file_type == 'sequence':
            # Handle EXR sequence (poster frame) - generate animated thumbnail
            animated_cache_file = animated_path / f"sequence_animated_{file_hash}.webp"

            if not animated_cache_file.exists():
                frames = extract_sequence_frames(job.file_path, job.entry_id, library_dir)
                if len(frames) > 1:
                    save_animated_webp(frames, animated_cache_file)
                    result['animated'] = True

        elif ext in {'.gif', '.webp', '.apng'}:
            # Handle animated images
            animated_cache_file = animated_path / f"animated_{file_hash}.webp"

            if not animated_cache_file.exists():
                frames = extract_animated_image_frames(job.file_path)
                if len(frames) > 1:
                    save_animated_webp(frames, animated_cache_file)
                    result['animated'] = True

        # Generate static thumbnail in hash-based subdirectory for filesystem optimization
        # Use first 2 chars of hash for folder name (256 folders = ~40k files each for 10M files)
        hash_prefix = file_hash[:2]
        static_folder = cache_path / hash_prefix
        static_folder.mkdir(exist_ok=True)

        static_cache_file = static_folder / f"{file_hash}.webp"
        if not static_cache_file.exists():
            static_thumb = generate_static_thumbnail(job.file_path, size=256)
            if static_thumb:
                static_thumb.save(static_cache_file, 'WEBP', quality=75, method=6)

        return result

    except Exception as e:
        return {'status': 'failed', 'error': str(e)}


def extract_video_frames(filepath: Path, max_frames: int = 5) -> List[Image.Image]:
    """Extract frames from video for animated thumbnail (optimized for 10M files)."""
    frames = []
    try:
        video = cv2.VideoCapture(str(filepath))
        total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))

        if total_frames <= 1:
            return []

        # Calculate frame positions
        step = max(1, total_frames // max_frames)
        positions = [i * step for i in range(min(max_frames, total_frames // step))]

        for pos in positions:
            video.set(cv2.CAP_PROP_POS_FRAMES, pos)
            success, frame = video.read()

            if success and frame is not None:
                # Resize directly to 256px for efficiency (not 640px)
                height, width = frame.shape[:2]
                if width > 256 or height > 256:
                    scale = min(256.0 / width, 256.0 / height)
                    new_width = int(width * scale)
                    new_height = int(height * scale)
                    frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)

                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_frame = Image.fromarray(frame_rgb)
                frames.append(pil_frame)

        video.release()

    except Exception as e:
        logger.error("Failed to extract video frames", filepath=filepath, error=e)

    return frames


def extract_sequence_frames(poster_path: Path, entry_id: int, library_dir: str, max_frames: int = 5) -> List[Image.Image]:
    """Extract frames from EXR sequence for animated thumbnail."""
    frames = []
    try:
        # This is a simplified version - in production you'd need to reconstruct the sequence registry
        # For now, we'll just use the EXR rendering for the single poster frame
        # You could extend this to actually find all sequence files and sample frames

        from tagstudio.core.utils.is_sequences import _render_exr_frame

        # For now, just render the poster frame multiple times with slight variations
        # In a full implementation, you'd find all sequence files and sample them
        poster_frame = _render_exr_frame(poster_path)
        if poster_frame:
            frames.append(poster_frame)
            # Add the same frame multiple times for now (placeholder)
            # In production, you'd load other sequence frames
            frames.append(poster_frame)

    except Exception as e:
        logger.error("Failed to extract sequence frames", poster_path=poster_path, error=e)

    return frames


def extract_animated_image_frames(filepath: Path, max_frames: int = 5) -> List[Image.Image]:
    """Extract frames from animated image (optimized for 10M files)."""
    frames = []
    try:
        with Image.open(filepath) as img:
            if not getattr(img, 'is_animated', False):
                return []

            frame_count = getattr(img, 'n_frames', 1)
            step = max(1, frame_count // max_frames)

            for i in range(0, frame_count, step):
                if len(frames) >= max_frames:
                    break

                img.seek(i)
                frame = img.convert('RGB')
                frames.append(frame.copy())

    except Exception as e:
        logger.error("Failed to extract animated image frames", filepath=filepath, error=e)

    return frames


def save_animated_webp(frames: List[Image.Image], output_path: Path):
    """Save frames as animated WebP."""
    if not frames:
        return

    # Ensure consistent size
    target_size = frames[0].size
    normalized_frames = []

    for frame in frames:
        if frame.size != target_size:
            frame = frame.resize(target_size, Image.Resampling.BILINEAR)
        normalized_frames.append(frame)

    # Save animated WebP with optimized compression for 10M files
    normalized_frames[0].save(
        output_path,
        format='WebP',
        save_all=True,
        append_images=normalized_frames[1:],
        duration=200,  # ms per frame
        loop=0,
        quality=70,  # Lower quality for smaller files
        method=6,  # Best compression (slower but 20-30% smaller)
        minimize_size=True
    )


def generate_static_thumbnail(filepath: Path, size: int = 256) -> Optional[Image.Image]:
    """Generate static thumbnail for any file type."""
    try:
        ext = filepath.suffix.lower()

        # Handle EXR files with special rendering
        if ext == '.exr':
            from tagstudio.core.utils.is_sequences import _render_exr_frame
            img = _render_exr_frame(filepath, max_size=size)
            if not img:
                return None
        elif MediaCategories.is_ext_in_category(ext, MediaCategories.IMAGE_TYPES):
            img = Image.open(filepath)
            if img.mode != 'RGB':
                img = img.convert('RGB')
        elif MediaCategories.is_ext_in_category(ext, MediaCategories.VIDEO_TYPES):
            # Extract first frame
            video = cv2.VideoCapture(str(filepath))
            success, frame = video.read()
            video.release()

            if not success:
                return None

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
        else:
            return None

        # Resize maintaining aspect ratio
        img.thumbnail((size, size), Image.Resampling.BILINEAR)
        return img

    except Exception as e:
        logger.error("Failed to generate static thumbnail", filepath=filepath, error=e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Generate thumbnails for TagStudio library")
    parser.add_argument("library_path", type=Path, help="Path to TagStudio library")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker processes")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for processing")
    parser.add_argument("--force", action="store_true", help="Regenerate all thumbnails")
    parser.add_argument("--types", nargs="+", choices=['video', 'sequence', 'image', 'other'],
                      help="File types to process")
    parser.add_argument("--memory-limit", type=int, default=2048, help="Memory limit in MB")

    args = parser.parse_args()

    # Open library
    library = Library()
    status = library.open_library(args.library_path)

    if not status.success:
        logger.error(f"Failed to open library: {status.message}")
        return 1

    try:
        # Create generator
        generator = OptimizedThumbnailGenerator(
            library=library,
            max_workers=args.workers,
            batch_size=args.batch_size,
            max_memory_mb=args.memory_limit
        )

        # Generate thumbnails
        stats = generator.generate_all(
            force_regenerate=args.force,
            file_types=args.types
        )

        # Print final statistics
        logger.info("=== GENERATION COMPLETE ===")
        logger.info(f"Total files: {stats.total_files}")
        logger.info(f"Processed: {stats.processed}")
        logger.info(f"Skipped (existing): {stats.skipped_existing}")
        logger.info(f"Failed: {stats.failed}")
        logger.info(f"Animated thumbnails: {stats.animated_created}")
        logger.info(f"Static thumbnails: {stats.static_created}")
        logger.info(f"Time taken: {stats.elapsed_time:.1f} seconds")
        logger.info(f"Rate: {stats.files_per_second:.1f} files/second")

    finally:
        library.close()

    return 0


if __name__ == "__main__":
    exit(main())