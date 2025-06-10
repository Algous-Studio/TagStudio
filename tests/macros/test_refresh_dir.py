from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tagstudio.core.enums import LibraryPrefs
from tagstudio.core.utils.refresh_dir import RefreshDirTracker

CWD = Path(__file__).parent


@pytest.mark.parametrize("exclude_mode", [True, False])
@pytest.mark.parametrize("library", [TemporaryDirectory()], indirect=True)
def test_refresh_new_files(library, exclude_mode):
    # Given
    library.set_prefs(LibraryPrefs.IS_EXCLUDE_LIST, exclude_mode)
    library.set_prefs(LibraryPrefs.EXTENSION_LIST, [".md"])
    registry = RefreshDirTracker(library=library)
    library.included_files.clear()
    (library.library_dir / "FOO.MD").touch()

    # When
    assert len(list(registry.refresh_dir(library.library_dir))) == 1

    # Then
    assert registry.files_not_in_library == [Path("FOO.MD")]


@pytest.mark.parametrize("library", [TemporaryDirectory()], indirect=True)
def test_refresh_exr_sequence(library):
    seq_dir = Path(__file__).parent / "../fixtures/exr_sequence"
    for f in seq_dir.iterdir():
        target = library.library_dir / f.name
        target.write_text("")
    registry = RefreshDirTracker(library=library)
    library.included_files.clear()

    list(registry.refresh_dir(library.library_dir))

    assert registry.files_not_in_library == [Path("test_sequence_0001.exr")]
    assert registry.sequence_counts.get(Path("test_sequence_0001.exr")) == 100
