from pathlib import Path

from youtube_audio_video_downloader import SUPPORTED_OPERATIONS


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "PYTHON_LIBRARY.md"
README = ROOT / "README.md"


def test_python_library_guide_documents_install_upgrade_and_every_operation() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "python -m pip install -U" in guide
    assert "youtube_media_studio-v<version>-py3-none-any.whl" in guide
    assert "python -m pip install -U youtube-media-studio" in guide
    assert "OperationSummary" in guide
    assert "CancellationToken" in guide
    assert "Author-email: dgkiitcsedual@gmail.com" in guide
    assert "License-Expression: MIT" in guide
    for operation in SUPPORTED_OPERATIONS:
        assert f"`{operation}`" in guide


def test_readme_links_to_python_library_guide() -> None:
    assert "docs/PYTHON_LIBRARY.md" in README.read_text(encoding="utf-8")


def test_python_library_guide_is_included_in_source_distribution() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "include docs/PYTHON_LIBRARY.md" in manifest.splitlines()
