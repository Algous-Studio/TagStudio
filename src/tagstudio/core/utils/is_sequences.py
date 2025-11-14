#!/usr/bin/env python3

"""
Этот код предназначен для обновления библиотеки TagStudio, добавляя поддержку секвенций.
Он проверяет наличие файлов с расширениями, которые могут быть частью секвенции,
и обновляет соответствующие записи в базе данных, устанавливая флаг is_sequence.

library_path должен указывать на путь к библиотеке TagStudio. Сменить, если нужно.
"""
import hashlib
import math
import re
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np
import structlog
from PIL import Image
from sqlalchemy import inspect, text, update
from sqlalchemy.orm import Session

from tagstudio.core.constants import THUMB_CACHE_NAME, TS_FOLDER_NAME
from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Entry

SEQUENCE_EXTENSIONS = {"dpx", "exr", "jpg", "jpeg", "png", "tif", "tiff", "ari", "tga"}
SEQUENCE_RE = re.compile(r"^(.*?)(?:[._-]?)(\d{3,6})$")

logger = structlog.get_logger(__name__)


class SequenceEntry:
    def __init__(self):
        self.entries = []

    @property
    def poster(self):
        return min(self.entries, key=lambda e: e.path) if self.entries else None

    @property
    def frame_count(self):
        return len(self.entries)


def _render_exr_frame(filepath: Path, max_size: int = 640) -> Optional[Image.Image]:
    """Render a single EXR frame with HDR tone mapping."""
    try:
        # Try importing OpenEXR libraries
        import Imath
        import OpenEXR
        
        # Open EXR file
        exr_file = OpenEXR.InputFile(str(filepath))
        header = exr_file.header()
        
        # Get image dimensions
        dw = header['dataWindow']
        width = dw.max.x - dw.min.x + 1
        height = dw.max.y - dw.min.y + 1
        
        # Get available channels
        channels = header['channels']
        
        # Read RGB channels (fallback to available channels if RGB not present)
        float_type = Imath.PixelType(Imath.PixelType.FLOAT)
        
        if 'R' in channels and 'G' in channels and 'B' in channels:
            # RGB channels available
            r_str = exr_file.channel('R', float_type)
            g_str = exr_file.channel('G', float_type)
            b_str = exr_file.channel('B', float_type)
            
            # Convert to numpy arrays
            r = np.frombuffer(r_str, dtype=np.float32).reshape(height, width)
            g = np.frombuffer(g_str, dtype=np.float32).reshape(height, width)
            b = np.frombuffer(b_str, dtype=np.float32).reshape(height, width)
            
            # Stack channels
            exr_image = np.stack([r, g, b], axis=2)
        
        elif 'Y' in channels:
            # Luminance channel only
            y_str = exr_file.channel('Y', float_type)
            y = np.frombuffer(y_str, dtype=np.float32).reshape(height, width)
            # Convert grayscale to RGB
            exr_image = np.stack([y, y, y], axis=2)
        
        else:
            # Use first available channel
            channel_name = list(channels.keys())[0]
            ch_str = exr_file.channel(channel_name, float_type)
            ch = np.frombuffer(ch_str, dtype=np.float32).reshape(height, width)
            exr_image = np.stack([ch, ch, ch], axis=2)
        
        # Apply tone mapping (simple gamma correction)
        gamma = 1.0 / 2.2
        exposure = 1.0
        
        # Handle NaN and infinite values
        exr_image = np.nan_to_num(exr_image, nan=0.0, posinf=1.0, neginf=0.0)
        
        # Apply exposure adjustment
        exr_image *= exposure
        
        # Clamp negative values and apply gamma correction
        exr_image = np.clip(exr_image, 0, None)
        exr_image = np.power(exr_image, gamma)
        
        # Simple tone mapping for very bright values
        # Use a simple reinhard-like curve: x / (1 + x)
        tone_mapped = exr_image / (1.0 + exr_image)
        
        # Handle any remaining NaN values
        tone_mapped = np.nan_to_num(tone_mapped, nan=0.0, posinf=1.0, neginf=0.0)
        
        # Convert to 8-bit
        ldr_image = np.clip(tone_mapped * 255, 0, 255).astype(np.uint8)
        
        # Convert to PIL Image
        pil_image = Image.fromarray(ldr_image, mode="RGB")
        
        # Resize if needed to keep memory usage reasonable
        if max(width, height) > max_size:
            # Calculate aspect-preserving size
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            
            pil_image = pil_image.resize(
                (new_width, new_height), resample=Image.Resampling.BILINEAR
            )
        
        return pil_image
        
    except Exception as e:
        logger.error("Failed to render EXR frame", filepath=filepath, error=type(e).__name__)
        # Fallback to standard PIL image handling
        try:
            pil_image = Image.open(filepath)
            if pil_image.mode != "RGB" and pil_image.mode != "RGBA":
                pil_image = pil_image.convert(mode="RGBA")
            if pil_image.mode == "RGBA":
                new_bg = Image.new("RGB", pil_image.size, color="#1e1e1e")
                new_bg.paste(pil_image, mask=pil_image.getchannel(3))
                pil_image = new_bg
            
            # Resize if needed
            width, height = pil_image.size
            if max(width, height) > max_size:
                if width > height:
                    new_width = max_size
                    new_height = int(height * (max_size / width))
                else:
                    new_height = max_size
                    new_width = int(width * (max_size / height))
                
                pil_image = pil_image.resize(
                    (new_width, new_height), resample=Image.Resampling.BILINEAR
                )
            
            return pil_image
        except Exception:
            return None
    
    return None


def create_sequence_animated_webp(
    sequence: SequenceEntry, library: Library, max_frames: int = 8
) -> Optional[bytes]:
    """Create animated WebP from EXR sequence frames."""
    if not sequence.entries:
        return None
    
    # Sample frames from the sequence
    total_frames = len(sequence.entries)
    if total_frames <= max_frames:
        # Use all frames
        selected_indices = list(range(total_frames))
    else:
        # Sample frames evenly across the sequence
        step = total_frames / max_frames
        selected_indices = [int(i * step) for i in range(max_frames)]
    
    frames = []
    for idx in selected_indices:
        if idx >= len(sequence.entries):
            continue
            
        entry = sequence.entries[idx]
        # Build full path using library directory
        if library.library_dir:
            full_path = library.library_dir / entry.path
        else:
            full_path = Path(entry.path)
        
        if not full_path.exists():
            logger.warning("Sequence frame not found", path=full_path)
            continue
        
        # Render the EXR frame
        if entry.suffix.lower() == "exr":
            frame_img = _render_exr_frame(full_path)
        else:
            # For other sequence formats, use standard PIL
            try:
                frame_img = Image.open(full_path)
                if frame_img.mode != "RGB":
                    frame_img = frame_img.convert("RGB")
            except Exception as e:
                logger.error("Failed to load sequence frame", path=full_path, error=e)
                continue
        
        if frame_img:
            print(f"[DEBUG] Frame {idx}: size={frame_img.size}, mode={frame_img.mode}")
            frames.append(frame_img)
    
    if len(frames) < 2:
        logger.warning("Not enough frames for animation", frame_count=len(frames))
        return None
    
    # Create animated WebP
    try:
        webp_buffer = BytesIO()
        
        # Ensure all frames have the same size - resize to first frame size
        target_size = frames[0].size
        print(f"[DEBUG] Target size for animation: {target_size}")
        
        resized_frames = []
        for i, frame in enumerate(frames):
            if frame.size != target_size:
                print(f"[DEBUG] Resizing frame {i} from {frame.size} to {target_size}")
                frame = frame.resize(target_size, Image.Resampling.BILINEAR)
            resized_frames.append(frame)
        
        # Calculate duration based on typical frame rates
        # For sequences, use a moderate animation speed
        frame_duration = 200  # 200ms = 5 FPS
        
        resized_frames[0].save(
            webp_buffer,
            format='WebP',
            save_all=True,
            append_images=resized_frames[1:],
            duration=frame_duration,
            loop=0,
            quality=75,  # Good quality for sequences
            method=4,  # Balanced compression
            lossless=False,
            minimize_size=True,
        )
        
        animated_bytes = webp_buffer.getvalue()
        logger.info(
            "Created animated WebP for sequence",
            frame_count=len(frames),
            size_bytes=len(animated_bytes)
        )
        return animated_bytes
        
    except Exception as e:
        logger.error("Failed to create animated WebP for sequence", error=e)
        return None


class SequenceRegistry:
    def __init__(self, library: Library):
        self.library = library
        self.sequences = []
        self.entry_to_sequence = {}

    def _get_sequence_hash(self, sequence: SequenceEntry) -> str:
        """Generate a hash for the sequence based on its files and modification times."""
        if not sequence.entries:
            return ""
        
        # Sort entries to ensure consistent hashing
        sorted_entries = sorted(sequence.entries, key=lambda e: e.path)
        
        # Create hash from sequence paths and modification times
        hash_parts = []
        for entry in sorted_entries:
            if self.library.library_dir:
                full_path = self.library.library_dir / entry.path
            else:
                full_path = Path(entry.path)
            
            mod_time = ""
            if full_path.exists():
                try:
                    mod_time = str(full_path.stat().st_mtime_ns)
                except Exception:
                    pass
            
            hash_parts.append(f"{entry.path}:{mod_time}")
        
        hash_string = "|".join(hash_parts)
        return hashlib.shake_128(hash_string.encode("utf-8")).hexdigest(8)

    def generate_sequence_thumbnails(self, force_regenerate: bool = False) -> int:
        """Generate animated WebP thumbnails for all EXR sequences."""
        if not self.library.library_dir:
            logger.error("Library directory not set, cannot generate sequence thumbnails")
            return 0
        
        cache_dir = self.library.library_dir / TS_FOLDER_NAME / THUMB_CACHE_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        generated_count = 0
        
        for sequence in self.sequences:
            # Only process sequences with EXR files
            exr_entries = [e for e in sequence.entries if e.suffix.lower() == "exr"]
            print(f"[DEBUG] Checking sequence with {len(sequence.entries)} entries")
            print(f"[DEBUG] EXR entries: {[e.path for e in exr_entries]}")
            if not exr_entries:
                print(f"[DEBUG] No EXR entries found, skipping sequence")
                continue
            
            # Generate cache filename
            seq_hash = self._get_sequence_hash(sequence)
            if not seq_hash:
                print(f"[DEBUG] No sequence hash generated, skipping")
                continue
            
            print(f"[DEBUG] Generated sequence hash: {seq_hash}")
            cache_filename = f"sequence_animated_{seq_hash}.webp"
            cache_path = cache_dir / cache_filename
            print(f"[DEBUG] Cache path: {cache_path}")
            
            # Skip if already exists and not forcing regeneration
            if cache_path.exists() and not force_regenerate:
                logger.debug("Sequence thumbnail already exists", cache_path=cache_path)
                continue
            
            print(f"[INFO] Generating animated thumbnail for EXR sequence")
            print(f"[INFO] Frame count: {len(sequence.entries)}")
            print(f"[INFO] Poster path: {sequence.poster.path if sequence.poster else 'unknown'}")
            
            # Generate the animated WebP
            animated_bytes = create_sequence_animated_webp(sequence, self.library)
            
            if animated_bytes:
                try:
                    with open(cache_path, 'wb') as f:
                        f.write(animated_bytes)
                    
                    logger.info(
                        "Saved animated sequence thumbnail",
                        cache_path=cache_path,
                        size_bytes=len(animated_bytes)
                    )
                    generated_count += 1
                    
                except Exception as e:
                    logger.error("Failed to save sequence thumbnail", cache_path=cache_path, error=e)
            else:
                logger.warning("Failed to generate animated WebP for sequence")
        
        return generated_count

    def get_sequence_thumbnail_path(self, sequence: SequenceEntry) -> Optional[Path]:
        """Get the path to the cached animated thumbnail for a sequence."""
        if not self.library.library_dir:
            return None
        
        seq_hash = self._get_sequence_hash(sequence)
        if not seq_hash:
            return None
        
        cache_dir = self.library.library_dir / TS_FOLDER_NAME / THUMB_CACHE_NAME
        cache_filename = f"sequence_animated_{seq_hash}.webp"
        cache_path = cache_dir / cache_filename
        
        return cache_path if cache_path.exists() else None

    def refresh_sequences(self):
        groups = defaultdict(SequenceEntry)

        # Get all entries from the database
        with Session(self.library.engine) as session:
            entries = session.query(Entry).all()

        for entry in entries:
            if entry.suffix.lower() not in SEQUENCE_EXTENSIONS:
                continue

            match = SEQUENCE_RE.match(entry.path.stem)
            if match:
                base = match.group(1)
                key = (entry.path.parent, base, entry.suffix)
                groups[key].entries.append(entry)

        self.sequences.clear()
        for seq in groups.values():
            if len(seq.entries) > 1:
                sorted_entries = sorted(seq.entries, key=lambda e: e.path)
                new_seq = SequenceEntry()
                new_seq.entries = sorted_entries
                self.sequences.append(new_seq)
                for e in sorted_entries:
                    self.entry_to_sequence[e.id] = new_seq


def ensure_is_sequence_column(library: Library):
    """Check if is_sequence column exists in entries table. If not - create it."""
    insp = inspect(library.engine)
    columns = [col['name'] for col in insp.get_columns("entries")]

    if "is_sequence" not in columns:
        print("[INFO] Adding is_sequence column to entries table...")
        with Session(library.engine) as session:
            session.execute(
                text("ALTER TABLE entries ADD COLUMN is_sequence BOOLEAN DEFAULT FALSE NOT NULL")
            )
            session.commit()
        print("[OK] is_sequence column added")
    else:
        print("[OK] is_sequence column already exists")


def update_sequences(library: Library, generate_thumbnails: bool = True):
    ensure_is_sequence_column(library)

    registry = SequenceRegistry(library)
    registry.refresh_sequences()
    with Session(library.engine) as session:
        session.execute(update(Entry).values(is_sequence=False))
        session.commit()

        updated_count = 0
        for seq in registry.sequences:
            for i, entry in enumerate(seq.entries):
                session.execute(
                    update(Entry)
                    .where(Entry.id == entry.id)
                    .values(is_sequence=(i != 0))
                )

                updated_count += 1
        session.commit()

    print(f"[OK] Updated {len(registry.sequences)} sequences, {updated_count} files")
    
    # Clear existing thumbnails for sequence files to force regeneration with animated logic
    invalidated_count = invalidate_sequence_thumbnails(library, registry)
    print(f"[OK] Invalidated {invalidated_count} existing sequence thumbnails")
    
    # Generate animated thumbnails for EXR sequences
    if generate_thumbnails:
        print("[INFO] Generating animated thumbnails for EXR sequences...")
        thumbnail_count = registry.generate_sequence_thumbnails()
        print(f"[OK] Generated {thumbnail_count} animated sequence thumbnails")


def invalidate_sequence_thumbnails(library: Library, registry: SequenceRegistry) -> int:
    """Invalidate existing static thumbnails for sequence files to force regeneration."""
    if not library.library_dir:
        return 0
    
    cache_dir = library.library_dir / TS_FOLDER_NAME / THUMB_CACHE_NAME
    if not cache_dir.exists():
        return 0
    
    invalidated_count = 0
    
    # Get all sequence entries
    sequence_files = set()
    for sequence in registry.sequences:
        for entry in sequence.entries:
            sequence_files.add(entry.path)
    
    # Find and remove existing static thumbnails for sequence files
    for thumb_file in cache_dir.glob("*.webp"):
        # Skip animated sequence thumbnails
        if thumb_file.name.startswith("sequence_animated_"):
            continue
            
        # Check if this thumbnail corresponds to a sequence file
        # Thumbnail names are typically hashed, so we need a different approach
        # For now, we'll remove all non-animated thumbnails to be safe
        try:
            thumb_file.unlink()
            invalidated_count += 1
        except Exception as e:
            logger.warning("Failed to remove thumbnail", path=thumb_file, error=e)
    
    return invalidated_count


def generate_sequence_thumbnails_only(library: Library, force_regenerate: bool = False):
    """Generate only animated thumbnails for existing sequences without updating database."""
    registry = SequenceRegistry(library)
    registry.refresh_sequences()
    
    print(f"[INFO] Found {len(registry.sequences)} sequences")
    print("[INFO] Generating animated thumbnails for EXR sequences...")
    
    thumbnail_count = registry.generate_sequence_thumbnails(force_regenerate=force_regenerate)
    print(f"[OK] Generated {thumbnail_count} animated sequence thumbnails")


if __name__ == "__main__":
    library_path = Path("D:/videos")

    lib = Library()
    status = lib.open_library(library_path)

    if not status.success:
        print("[ERROR] Failed to open library:", status.message or "Unknown error")
    else:
        update_sequences(lib)
        lib.close()




"""
Этот закомментированный код использовать, если раскомментированный код не работает.
Но с ним нужно будет вручную добавить в таблицу entries колонку is_sequence.
tagstudio_db=# ALTER TABLE entries ADD COLUMN is_sequence BOOLEAN DEFAULT FALSE NOT NULL;
Хотя существующий код протестирован и работает.
"""




# #!/usr/bin/env python3
# import re
# from collections import defaultdict
# from pathlib import Path
# from sqlalchemy.orm import Session
# from sqlalchemy import update

# from tagstudio.core.library.alchemy.models import Entry
# from tagstudio.core.library.alchemy.library import Library

# SEQUENCE_EXTENSIONS = {"dpx", "exr", "jpg", "jpeg", "png", "tif", "tiff"}

# SEQUENCE_RE = re.compile(r"^(.*?)(?:[._-]?)(\d{3,6})$")


# class SequenceEntry:
#     def __init__(self):
#         self.entries = []

#     @property
#     def poster(self):
#         return min(self.entries, key=lambda e: e.path) if self.entries else None

#     @property
#     def frame_count(self):
#         return len(self.entries)


# class SequenceRegistry:
#     def __init__(self, library: Library):
#         self.library = library
#         self.sequences = []
#         self.entry_to_sequence = {}

#     def refresh_sequences(self):
#         groups = defaultdict(SequenceEntry)
#         print(1)
#         for entry in self.library.get_entries():
#             if entry.suffix.lower() not in SEQUENCE_EXTENSIONS:
#                 continue

#             match = SEQUENCE_RE.match(entry.path.stem)
#             if match:
#                 base = match.group(1)
#                 key = (entry.path.parent, base, entry.suffix)
#                 groups[key].entries.append(entry)
#         print(2)
#         self.sequences.clear()
#         for seq in groups.values():
#             if len(seq.entries) > 1:
#                 sorted_entries = sorted(seq.entries, key=lambda e: e.path)
#                 new_seq = SequenceEntry()
#                 new_seq.entries = sorted_entries
#                 self.sequences.append(new_seq)
#                 for e in sorted_entries:
#                     self.entry_to_sequence[e.id] = new_seq
        


# def update_sequences(library: Library):
#     registry = SequenceRegistry(library)
#     registry.refresh_sequences()
#     print(3)
#     with Session(library.engine) as session:
#         session.execute(update(Entry).values(is_sequence=False))
#         session.commit()

#         updated_count = 0
#         for seq in registry.sequences:
#             for i, entry in enumerate(seq.entries):
#                 session.execute(
#                     update(Entry)
#                     .where(Entry.id == entry.id)
#                     .values(is_sequence=(i != 0))
#                 )
#                 updated_count += 1
#         session.commit()

#     print(f"[OK] Обновлено {len(registry.sequences)} секвенций, {updated_count} файлов")


# if __name__ == "__main__":
#     # library_path = Path("/home/timur/Dev/library_tagstudio_test")
#     library_path = Path("/studio/stock")


#     lib = Library()
#     status = lib.open_library(library_path)

#     if not status.success:
#         print("[ERROR] Не удалось открыть библиотеку:", status.message or "Неизвестная ошибка")
#     else:
#         update_sequences(lib)
#         lib.close()
