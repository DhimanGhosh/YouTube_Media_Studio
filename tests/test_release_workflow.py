from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "cross-platform-build.yml"
)


def test_every_successful_main_push_builds_and_publishes_a_versioned_release() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "push:\n    branches: [main]" in workflow
    assert "QT_QPA_PLATFORM: offscreen" in workflow
    assert workflow.count("sudo apt-get install --yes libegl1 libpulse0") == 2
    assert "needs: [quality, prepare-version]" in workflow
    assert 'uv version "${{ needs.prepare-version.outputs.version }}"' in workflow
    assert "target: windows" in workflow
    assert "target: linux" in workflow
    assert workflow.count("target: macos") == 2
    assert "needs: [prepare-version, python-and-raspi, desktop]" in workflow
    assert 'git tag -a "v${VERSION}"' in workflow
    assert 'gh release create "v${VERSION}"' in workflow
    assert "dist/*-Setup.exe" in workflow
    assert "dist/*-installer.run" in workflow
    assert "dist/*-installer.dmg" in workflow
    assert "SHA256SUMS.txt" in workflow
