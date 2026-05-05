"""mono int16 PCM 线性插值重采样工具。

抽离自 live_ptt 与 audio/playback：上行采集（24 kHz 麦克风 → 16 kHz Whisper）
与下行播放（16 kHz Edge-TTS → 48 kHz 耳机）共用同一份重采样实现，避免重复代码
与采样率协商不一致触发的 PortAudio paramErr=-50。
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Int16Array = npt.NDArray[np.int16]


def resample_int16_mono(
    samples: Int16Array,
    *,
    source_rate_hz: int,
    target_rate_hz: int,
) -> Int16Array:
    """用线性插值把 mono int16 PCM 重采样到目标采样率。

    采样率相同或输入为空时原样返回，避免不必要的拷贝；其他情况下按
    `target_rate_hz / source_rate_hz` 的比例计算目标长度并做线性插值。
    """
    source = np.asarray(samples, dtype=np.int16).reshape(-1)
    if source.size == 0 or source_rate_hz == target_rate_hz:
        return source
    target_size = int(round(source.size * target_rate_hz / source_rate_hz))
    if target_size <= 0:
        return np.array([], dtype=np.int16)
    source_positions = np.arange(source.size, dtype=np.float32)
    target_positions = np.linspace(0, source.size - 1, num=target_size, dtype=np.float32)
    resampled = np.interp(target_positions, source_positions, source.astype(np.float32))
    return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)
