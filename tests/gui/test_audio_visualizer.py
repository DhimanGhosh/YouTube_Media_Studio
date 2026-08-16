from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from youtube_audio_video_downloader.gui.media.audio_visualizer import (  # noqa: E402
    BAR_COUNT,
    IDLE_LEVEL,
    MusicVisualizer,
    spectrum_levels,
)


class AudioVisualizerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_pcm_frequency_content_produces_non_idle_spectrum(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48_000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        time_axis = np.arange(4096, dtype=np.float32) / 48_000
        signal = np.sin(2 * np.pi * 440 * time_axis) * 0.7
        stereo = np.column_stack((signal, signal))
        pcm = (stereo * 32767).astype(np.int16).tobytes()

        levels = spectrum_levels(QAudioBuffer(pcm, audio_format))

        self.assertEqual(len(levels), BAR_COUNT)
        self.assertGreater(max(levels), 0.7)
        self.assertTrue(all(IDLE_LEVEL <= value <= 1.0 for value in levels))

    def test_short_pcm_buffers_populate_every_logarithmic_band(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(44_100)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Float)
        noise = np.random.default_rng(7).normal(0.0, 0.2, (512, 2)).astype(np.float32)

        levels = spectrum_levels(QAudioBuffer(noise.tobytes(), audio_format))

        self.assertEqual(len(levels), BAR_COUNT)
        self.assertTrue(all(value > IDLE_LEVEL for value in levels[:8]))

    def test_widget_is_flat_when_idle_and_eases_toward_live_levels(self) -> None:
        widget = MusicVisualizer()
        self.assertEqual(widget._levels, [IDLE_LEVEL] * BAR_COUNT)

        widget.set_playing(True)
        widget.set_levels([0.8] * BAR_COUNT)
        widget._animate()
        self.assertTrue(all(value > IDLE_LEVEL for value in widget._levels))

        widget.set_playing(False)
        for _ in range(40):
            widget._animate()
        self.assertTrue(all(abs(value - IDLE_LEVEL) < 0.001 for value in widget._levels))
        widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
