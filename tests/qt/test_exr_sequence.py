from pathlib import Path

from tagstudio.qt.widgets.preview.preview_thumb import PreviewThumb
from tagstudio.qt.widgets.thumb_renderer import ThumbRenderer


def test_play_exr_sequence(qt_driver, library):
    seq_dir = Path(__file__).parent / "../fixtures/exr_sequence"
    first = seq_dir / "test_sequence_0001.exr"
    target = library.library_dir / first.name
    target.write_text("")
    tracker = ThumbRenderer(library)
    tracker.updated.emit(0.0, None, None, target)
    preview = PreviewThumb(library, qt_driver)
    preview.update_preview(target)
