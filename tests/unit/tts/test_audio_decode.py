"""Edge-TTS 音频解码测试。"""

import wave
from pathlib import Path

import numpy as np

from teams_voice_interpreter.tts.audio_decode import decode_mp3_bytes_to_pcm16


def test_decode_mp3_bytes_to_pcm16_uses_afconvert_runner(tmp_path: Path) -> None:
    """解码器必须通过可替换 runner 转成 16 kHz mono PCM。"""

    def fake_runner(command: list[str]) -> None:
        output_path = Path(command[-1])
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.array([0, 1000, -1000], dtype=np.int16).tobytes())

    pcm = decode_mp3_bytes_to_pcm16(b"mp3", temp_dir=tmp_path, runner=fake_runner)

    assert pcm.tolist() == [0, 1000, -1000]
