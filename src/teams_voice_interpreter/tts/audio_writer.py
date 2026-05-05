"""TTS 音频 chunk 写出到目标设备。"""

from __future__ import annotations

import numpy as np

from teams_voice_interpreter.audio.playback import BlackHoleWriter, DefaultOutputWriter
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent


class StreamAudioWriter:
    """把 TTS chunk 转为 PCM 并写入对应方向的输出。"""

    def __init__(
        self,
        *,
        blackhole_writer: BlackHoleWriter | None = None,
        default_writer: DefaultOutputWriter | None = None,
    ) -> None:
        self.blackhole_writer = blackhole_writer or BlackHoleWriter()
        self.default_writer = default_writer or DefaultOutputWriter()

    def write_events(self, events: list[TTSEvent], *, direction: AudioDirection) -> int:
        """写出所有包含音频的事件并返回累计字节数。"""
        total_bytes = 0
        for event in events:
            if not event.audio_chunk:
                continue
            pcm = decode_audio_chunk(event.audio_chunk)
            if direction is AudioDirection.UPLINK:
                self.blackhole_writer.write_mono(pcm)
            else:
                self.default_writer.write_mono(pcm)
            total_bytes += pcm.nbytes
        return total_bytes


def decode_audio_chunk(chunk: bytes) -> np.ndarray:
    """测试用音频解码：把任意 bytes 映射为 int16 PCM。"""
    if not chunk:
        return np.array([], dtype=np.int16)
    raw = np.frombuffer(chunk, dtype=np.uint8).astype(np.int16)
    return (raw - 128).astype(np.int16)
