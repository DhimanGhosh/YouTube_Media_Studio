from __future__ import annotations

import unittest
from pathlib import Path

from youtube_audio_video_downloader.core.file_access import (
    FileInUseSkippedError,
    file_in_use_handler,
    retry_file_operation,
)


class FileAccessTest(unittest.TestCase):
    def test_prompts_then_retries_locked_file(self) -> None:
        attempts = 0
        prompts: list[tuple[Path, str]] = []

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError(13, "file is in use")
            return "done"

        with file_in_use_handler(lambda path, action: prompts.append((path, action))):
            result = retry_file_operation("song.mp3", "updating it", operation)

        self.assertEqual(result, "done")
        self.assertEqual(attempts, 2)
        self.assertEqual(prompts, [(Path("song.mp3"), "updating it")])

    def test_second_lock_marks_only_that_file_skipped(self) -> None:
        prompts = 0

        def prompt(_path: Path, _action: str) -> None:
            nonlocal prompts
            prompts += 1

        def locked_operation() -> None:
            raise PermissionError(13, "file is still in use")

        with file_in_use_handler(prompt):
            with self.assertRaises(FileInUseSkippedError):
                retry_file_operation("song.mp3", "moving it", locked_operation)

        self.assertEqual(prompts, 1)

    def test_unrelated_os_errors_are_not_prompted(self) -> None:
        with file_in_use_handler(lambda _path, _action: self.fail("unexpected prompt")):
            with self.assertRaises(FileNotFoundError):
                retry_file_operation(
                    "missing.mp3",
                    "reading it",
                    lambda: (_ for _ in ()).throw(FileNotFoundError("missing")),
                )


if __name__ == "__main__":
    unittest.main()
