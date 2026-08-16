"""Threaded PCM spectrum analysis and a lightweight sidebar renderer."""

from __future__ import annotations

import time

import numpy as np
from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat
from PyQt6.QtWidgets import QWidget


BAR_COUNT = 28
IDLE_LEVEL = 0.1


class SpectrumAnalyzer(QObject):
    """Convert decoded PCM buffers into logarithmic frequency-band levels."""

    spectrum_ready = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._last_analysis_at = 0.0

    @pyqtSlot(object)
    def analyze(self, buffer: QAudioBuffer) -> None:
        """Analyze at most 30 buffers per second on the worker thread."""

        now = time.monotonic()
        if now - self._last_analysis_at < 1 / 30:
            return
        self._last_analysis_at = now
        levels = spectrum_levels(buffer)
        if levels:
            self.spectrum_ready.emit(levels)


def spectrum_levels(buffer: QAudioBuffer, bars: int = BAR_COUNT) -> list[float]:
    """Return normalized FFT bands for one interleaved Qt audio buffer."""

    if not buffer.isValid() or buffer.byteCount() <= 0:
        return []
    audio_format = buffer.format()
    channels = max(1, audio_format.channelCount())
    sample_rate = audio_format.sampleRate()
    dtype_and_scale = {
        QAudioFormat.SampleFormat.UInt8: (np.uint8, 128.0),
        QAudioFormat.SampleFormat.Int16: (np.int16, 32768.0),
        QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648.0),
        QAudioFormat.SampleFormat.Float: (np.float32, 1.0),
    }.get(audio_format.sampleFormat())
    if dtype_and_scale is None or sample_rate <= 0:
        return []
    dtype, scale = dtype_and_scale
    raw = buffer.constData().asstring(buffer.byteCount())
    samples = np.frombuffer(raw, dtype=dtype)
    usable = samples.size - (samples.size % channels)
    if usable < channels * 256:
        return []
    samples = samples[:usable].astype(np.float32, copy=False)
    if dtype is np.uint8:
        samples = samples - 128.0
    mono = samples.reshape(-1, channels).mean(axis=1) / scale
    mono = mono[-min(4096, mono.size):]
    mono = mono - float(np.mean(mono))
    if float(np.sqrt(np.mean(mono * mono))) < 0.0005:
        return [IDLE_LEVEL] * bars

    window = np.hanning(mono.size).astype(np.float32)
    # Qt commonly delivers 512- or 1024-frame buffers. Their native FFT bins
    # are wider than the first logarithmic bands and used to leave alternating
    # low bars empty. Zero-padding provides a continuous spectral envelope
    # without inventing energy or adding latency to playback.
    fft_size = max(8192, 1 << (mono.size - 1).bit_length())
    magnitude = (
        2.0 * np.abs(np.fft.rfft(mono * window, n=fft_size))
        / max(float(window.sum()), 1.0)
    )
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
    upper = min(16_000.0, sample_rate / 2.0)
    if upper <= 55.0:
        return []
    edges = np.geomspace(55.0, upper, bars + 1)
    output: list[float] = []
    for low, high in zip(edges[:-1], edges[1:]):
        values = magnitude[(frequencies >= low) & (frequencies < high)]
        if values.size:
            amplitude = float(values.max())
        else:
            # Very low sample rates can still produce a sub-bin band. Sample
            # the interpolated envelope at its geometric center in that case.
            center = float(np.sqrt(low * high))
            amplitude = float(np.interp(center, frequencies, magnitude))
        decibels = 20.0 * np.log10(max(amplitude, 1e-7))
        output.append(float(np.clip((decibels + 60.0) / 54.0, IDLE_LEVEL, 1.0)))
    return output


class MusicVisualizer(QWidget):
    """Paint a sleek spectrum that idles at ten percent when playback stops."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(82)
        self.setAccessibleName("Live music spectrum")
        self.setToolTip("Live frequency spectrum from Media Library playback")
        self._playing = False
        self._last_levels_at = 0.0
        self._levels = [IDLE_LEVEL] * BAR_COUNT
        self._targets = [IDLE_LEVEL] * BAR_COUNT
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    @pyqtSlot(object)
    def set_levels(self, values: object) -> None:
        levels = [float(value) for value in list(values)[:BAR_COUNT]]
        if len(levels) != BAR_COUNT:
            return
        self._targets = [min(1.0, max(IDLE_LEVEL, value)) for value in levels]
        self._last_levels_at = time.monotonic()

    @pyqtSlot(bool)
    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        if not self._playing:
            self._targets = [IDLE_LEVEL] * BAR_COUNT

    def _animate(self) -> None:
        if not self._playing or time.monotonic() - self._last_levels_at > 0.3:
            self._targets = [IDLE_LEVEL] * BAR_COUNT
        changed = False
        for index, target in enumerate(self._targets):
            current = self._levels[index]
            updated = current + (target - current) * (0.46 if target >= current else 0.2)
            if abs(updated - current) > 0.001:
                changed = True
            self._levels[index] = updated
        if changed:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect().adjusted(5, 8, -5, -10)
        baseline = float(bounds.bottom())
        painter.setPen(QPen(QColor(132, 153, 222, 42), 1.0))
        painter.drawLine(bounds.left(), int(baseline), bounds.right(), int(baseline))
        gap = 2.2
        width = max(2.0, (bounds.width() - gap * (BAR_COUNT - 1)) / BAR_COUNT)
        maximum_height = max(1.0, bounds.height() - 2.0)
        gradient = QLinearGradient(0.0, float(bounds.top()), 0.0, baseline)
        gradient.setColorAt(0.0, QColor("#66f0ff"))
        gradient.setColorAt(0.48, QColor("#7c8cff"))
        gradient.setColorAt(1.0, QColor("#a66cff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        x = float(bounds.left())
        for level in self._levels:
            height = max(3.0, maximum_height * level)
            painter.drawRoundedRect(
                QRectF(x, baseline - height, width, height),
                min(width / 2.0, 2.5),
                min(width / 2.0, 2.5),
            )
            x += width + gap
        painter.end()
