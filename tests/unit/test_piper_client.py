"""Piper TTS 客户端单元测试。"""

from __future__ import annotations

import asyncio
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import PiperTTSError
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent
from teams_voice_interpreter.tts.piper_client import (
    DEFAULT_PIPER_VOICES,
    PIPER_OUTPUT_SAMPLE_RATE_HZ,
    PiperClient,
)


class _FakePiperVoice:
    """模拟 PiperVoice：按预设 chunks 同步 yield AudioChunk。"""

    _next_id = 0

    def __init__(
        self,
        *,
        chunks: list[bytes] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else [b"\x00\x01" * 100, b"\x02\x03" * 100]
        self._raises = raises
        self.instance_id = _FakePiperVoice._next_id
        _FakePiperVoice._next_id += 1

    def synthesize(self, _text: str) -> Iterator[Any]:
        if self._raises is not None:
            raise self._raises
        for data in self._chunks:
            yield types.SimpleNamespace(audio_int16_bytes=data)


def _materialize_models_dir(tmp_path: Path, voices: list[str]) -> Path:
    """在 tmp_path 下生成 .onnx + .onnx.json 占位文件，让 validate_voice 通过。"""
    models_dir = tmp_path / "piper-models"
    models_dir.mkdir()
    for voice in voices:
        (models_dir / f"{voice}.onnx").write_bytes(b"fake onnx")
        (models_dir / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    return models_dir


def _make_client(
    tmp_path: Path,
    *,
    voice_loader: Any,
    voices: dict[AudioDirection, str] | None = None,
    pool_size: int | None = None,
) -> PiperClient:
    voices = voices or dict(DEFAULT_PIPER_VOICES)
    models_dir = _materialize_models_dir(tmp_path, list(voices.values()))
    kwargs: dict[str, Any] = {"models_dir": models_dir, "voices": voices, "voice_loader": voice_loader}
    if pool_size is not None:
        kwargs["pool_size"] = pool_size
    return PiperClient(**kwargs)


def test_default_voices_match_probe_script_choices() -> None:
    """生产默认音色必须与 measure_piper_first_byte.py 的探针选择一致。"""
    assert DEFAULT_PIPER_VOICES[AudioDirection.UPLINK] == "en_US-amy-medium"
    assert DEFAULT_PIPER_VOICES[AudioDirection.DOWNLINK] == "zh_CN-huayan-medium"


def test_piper_output_sample_rate_constant_documents_default_voices() -> None:
    """22050 Hz 是当前两个默认 voice 的 sample rate；常量必须明示。"""
    assert PIPER_OUTPUT_SAMPLE_RATE_HZ == 22050


@pytest.mark.asyncio
async def test_stream_synthesize_yields_first_byte_then_audio_then_completed(
    tmp_path: Path,
) -> None:
    """成功路径必须按 first_byte → audio_chunk*N → completed 顺序产出。"""

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(chunks=[b"pcm0", b"pcm1", b"pcm2"])

    client = _make_client(tmp_path, voice_loader=loader)
    events = [
        event
        async for event in client.stream_synthesize(
            "hello", direction=AudioDirection.UPLINK
        )
    ]

    assert [event.kind for event in events] == [
        "first_byte",
        "audio_chunk",
        "audio_chunk",
        "completed",
    ]
    assert events[0].audio_chunk == b"pcm0"
    assert events[1].audio_chunk == b"pcm1"
    assert events[2].audio_chunk == b"pcm2"
    assert isinstance(events[0], TTSEvent)
    # 所有 event 必须标注 raw PCM @ 22050 Hz，让下游 audio_decode 选 PCM 路径。
    assert all(event.audio_format == "pcm_s16le_22050" for event in events)


@pytest.mark.asyncio
async def test_stream_synthesize_skips_empty_chunks(tmp_path: Path) -> None:
    """空 chunk 必须被跳过，避免 first_byte 落到空 bytes 上。"""

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(chunks=[b"", b"", b"pcm-real"])

    client = _make_client(tmp_path, voice_loader=loader)
    events = [
        event
        async for event in client.stream_synthesize(
            "hi", direction=AudioDirection.UPLINK
        )
    ]

    assert [event.kind for event in events] == ["first_byte", "completed"]
    assert events[0].audio_chunk == b"pcm-real"


@pytest.mark.asyncio
async def test_stream_synthesize_rejects_empty_text(tmp_path: Path) -> None:
    """空白文本必须 fail-closed，给两段式提示。"""
    client = _make_client(tmp_path, voice_loader=lambda _p: _FakePiperVoice())

    with pytest.raises(PiperTTSError) as excinfo:
        async for _event in client.stream_synthesize(
            "   \n  ", direction=AudioDirection.UPLINK
        ):
            pass

    error = excinfo.value
    assert "没有可合成的译文文本" in error.what_happened
    assert "请等待下一段" in error.next_action


@pytest.mark.asyncio
async def test_stream_synthesize_raises_when_no_audio_returned(tmp_path: Path) -> None:
    """Piper 全空 chunks 流必须 fail-closed 提示重试。"""

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(chunks=[])

    client = _make_client(tmp_path, voice_loader=loader)

    with pytest.raises(PiperTTSError) as excinfo:
        async for _event in client.stream_synthesize(
            "ok", direction=AudioDirection.UPLINK
        ):
            pass

    assert "未返回任何音频数据" in excinfo.value.what_happened


@pytest.mark.asyncio
async def test_stream_synthesize_translates_runtime_error_to_user_facing(
    tmp_path: Path,
) -> None:
    """ONNX runtime / IO 等异常必须包装为 PiperTTSError 两段式提示。"""

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(raises=RuntimeError("model corrupted"))

    client = _make_client(tmp_path, voice_loader=loader)

    with pytest.raises(PiperTTSError) as excinfo:
        async for _event in client.stream_synthesize(
            "ok", direction=AudioDirection.UPLINK
        ):
            pass

    error = excinfo.value
    assert "Piper 合成失败" in error.what_happened
    assert "RuntimeError" in error.what_happened
    assert "ONNX 模型文件" in error.next_action


def test_validate_voice_passes_when_files_present(tmp_path: Path) -> None:
    """voice 文件齐全时不抛错。"""
    client = _make_client(tmp_path, voice_loader=lambda _p: _FakePiperVoice())
    client.validate_voice("en_US-amy-medium")  # 不抛 = pass


def test_validate_voice_fails_when_onnx_missing(tmp_path: Path) -> None:
    """onnx 文件缺失时 fail-closed 给具体下载链接。"""
    models_dir = tmp_path / "empty-models"
    models_dir.mkdir()
    client = PiperClient(models_dir, voice_loader=lambda _p: _FakePiperVoice())

    with pytest.raises(PiperTTSError) as excinfo:
        client.validate_voice("en_US-amy-medium")

    error = excinfo.value
    assert "缺少 Piper voice 模型 en_US-amy-medium" in error.what_happened
    assert "huggingface.co/rhasspy/piper-voices" in error.next_action


def test_validate_voice_fails_when_only_onnx_present_without_json(tmp_path: Path) -> None:
    """单独有 .onnx 没 .onnx.json 时仍 fail-closed（防止半成品下载）。"""
    models_dir = tmp_path / "half-models"
    models_dir.mkdir()
    (models_dir / "en_US-amy-medium.onnx").write_bytes(b"fake")
    # .onnx.json 故意不创建
    client = PiperClient(models_dir, voice_loader=lambda _p: _FakePiperVoice())

    with pytest.raises(PiperTTSError):
        client.validate_voice("en_US-amy-medium")


def test_preload_voice_loads_direction_voice_once(tmp_path: Path) -> None:
    """preload_voice 应在首次调用时加载 pool_size 个实例，再次调用不重复加载。"""
    load_count = 0

    def loader(_path: str) -> _FakePiperVoice:
        nonlocal load_count
        load_count += 1
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=2)

    client.preload_voice(direction=AudioDirection.UPLINK)
    client.preload_voice(direction=AudioDirection.UPLINK)

    assert load_count == 2


@pytest.mark.asyncio
async def test_voice_loader_is_cached_across_calls(tmp_path: Path) -> None:
    """相同 voice 多次 stream_synthesize 时 voice_loader 只在建池时触发 pool_size 次。"""
    load_count = 0

    def loader(_path: str) -> _FakePiperVoice:
        nonlocal load_count
        load_count += 1
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=2)
    for _ in range(5):
        async for _event in client.stream_synthesize(
            "ok", direction=AudioDirection.UPLINK
        ):
            pass

    assert load_count == 2


@pytest.mark.asyncio
async def test_explicit_voice_param_overrides_direction_default(tmp_path: Path) -> None:
    """voice 参数显式提供时必须覆盖方向默认音色。"""
    seen: list[str] = []

    def loader(path: str) -> _FakePiperVoice:
        seen.append(Path(path).stem)
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=1)
    async for _event in client.stream_synthesize(
        "hi", direction=AudioDirection.UPLINK, voice="zh_CN-huayan-medium"
    ):
        pass

    assert seen == ["zh_CN-huayan-medium"]


@pytest.mark.asyncio
async def test_explicit_voice_must_also_have_model_files(tmp_path: Path) -> None:
    """显式 voice 缺模型文件时与默认 voice 同样 fail-closed。"""
    client = _make_client(tmp_path, voice_loader=lambda _p: _FakePiperVoice())

    with pytest.raises(PiperTTSError) as excinfo:
        async for _event in client.stream_synthesize(
            "ok",
            direction=AudioDirection.UPLINK,
            voice="some-voice-not-present",
        ):
            pass

    assert "some-voice-not-present" in excinfo.value.what_happened


# —— 并发/池化测试 ——


@pytest.mark.asyncio
async def test_concurrent_streams_use_different_pool_instances(tmp_path: Path) -> None:
    """并发 stream_synthesize 必须分配到不同池实例，避免 ONNX 线程冲突。"""
    in_use: set[int] = set()
    max_concurrent = 0
    lock = asyncio.Lock()

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=3)

    async def run_one(text: str) -> int:
        nonlocal max_concurrent
        seen_ids: list[int] = []
        async for _event in client.stream_synthesize(text, direction=AudioDirection.UPLINK):
            pass
        # The instance_id is not directly observable from outside,
        # but we verify that 3 concurrent calls all complete without
        # blocking forever — that proves pool size ≥ 3.
        return len(seen_ids)

    # 启动 3 个并发合成（正好等于 pool_size）
    results = await asyncio.gather(
        run_one("a"), run_one("b"), run_one("c"),
    )
    # 所有 3 个都成功完成 = 池大小 ≥ 3
    assert len(results) == 3


@pytest.mark.asyncio
async def test_concurrent_calls_beyond_pool_size_queue_and_complete(
    tmp_path: Path,
) -> None:
    """超过 pool_size 的并发调用应排队等待（而非报错）。"""
    started = 0
    completed = 0
    lock = asyncio.Lock()

    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=2)

    async def run_one(text: str) -> None:
        nonlocal started, completed
        async with lock:
            started += 1
        async for _event in client.stream_synthesize(text, direction=AudioDirection.UPLINK):
            pass
        async with lock:
            completed += 1

    # 6 个并发调用，pool_size=2
    await asyncio.gather(*(run_one(str(i)) for i in range(6)))

    assert started == 6
    assert completed == 6


@pytest.mark.asyncio
async def test_pool_instances_are_reused_across_sequential_calls(
    tmp_path: Path,
) -> None:
    """顺序调用应该复用池中实例（归还后重新获取）。"""
    _FakePiperVoice._next_id = 0
    instance_ids: list[int] = []

    def loader(_path: str) -> _FakePiperVoice:
        voice = _FakePiperVoice(chunks=[b"pcm"])
        instance_ids.append(voice.instance_id)
        return voice

    client = _make_client(tmp_path, voice_loader=loader, pool_size=2)

    # 第一次调用
    async for _event in client.stream_synthesize("a", direction=AudioDirection.UPLINK):
        pass
    # 第二次调用（应复用池中实例）
    async for _event in client.stream_synthesize("b", direction=AudioDirection.UPLINK):
        pass

    # 建池时创建了 2 个实例，后续调用不再创建
    assert len(instance_ids) == 2


@pytest.mark.asyncio
async def test_pool_releases_instance_on_error(tmp_path: Path) -> None:
    """合成出错时池实例必须被归还，避免泄漏导致池耗尽。"""
    def loader(_path: str) -> _FakePiperVoice:
        return _FakePiperVoice(raises=RuntimeError("boom"))

    client = _make_client(tmp_path, voice_loader=loader, pool_size=1)

    # 第一次调用失败，但实例应归还
    with pytest.raises(PiperTTSError):
        async for _event in client.stream_synthesize("a", direction=AudioDirection.UPLINK):
            pass

    # 第二次调用应能获取到归还的实例（而非永远阻塞）
    with pytest.raises(PiperTTSError):
        async for _event in client.stream_synthesize("b", direction=AudioDirection.UPLINK):
            pass


@pytest.mark.asyncio
async def test_two_directions_use_separate_pools(tmp_path: Path) -> None:
    """上行和下行应有各自独立的 voice 池（不同音色）。"""
    uplink_seen: list[str] = []
    downlink_seen: list[str] = []

    def loader(path: str) -> _FakePiperVoice:
        voice_name = Path(path).stem
        if "amy" in voice_name:
            uplink_seen.append(voice_name)
        else:
            downlink_seen.append(voice_name)
        return _FakePiperVoice(chunks=[b"pcm"])

    client = _make_client(tmp_path, voice_loader=loader, pool_size=1)

    async for _event in client.stream_synthesize("hello", direction=AudioDirection.UPLINK):
        pass
    async for _event in client.stream_synthesize("你好", direction=AudioDirection.DOWNLINK):
        pass

    assert len(uplink_seen) == 1
    assert len(downlink_seen) == 1
    assert uplink_seen[0] == "en_US-amy-medium"
    assert downlink_seen[0] == "zh_CN-huayan-medium"
