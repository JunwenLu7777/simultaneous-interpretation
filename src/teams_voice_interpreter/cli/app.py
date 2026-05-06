"""Typer CLI 入口。"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
import typer
import uvicorn

from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.config import load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_ptt import (
    LivePushToTalkBridge,
    StreamingAudioRecorder,
    StreamingBlackHoleRecorder,
    StreamingMicrophoneRecorder,
)
from teams_voice_interpreter.live_say import LiveSayBridge, PreparedSayResult, SayResult
from teams_voice_interpreter.readiness import CheckStatus, ReadinessChecker, ReadinessReport
from teams_voice_interpreter.session.manager import DEFAULT_MANAGER
from teams_voice_interpreter.stt.vad import (
    SileroBackend,
    VadBackendProtocol,
    WebRtcBackend,
)
from teams_voice_interpreter.tts.audio_decode import warm_up_pyav_decoder

app = typer.Typer(help="Teams 双向实时语音同传桥")
DoctorMode = Literal["phrase", "realtime"]
DirectionOption = Literal["auto", "uplink", "downlink"]
DIRECTION_CLI_OPTION = typer.Option(
    "auto",
    "--direction",
    help="翻译方向：auto 按 target 推断；uplink 中文到英文；downlink 英文到中文。",
)
CHUNK_SECONDS_CLI_OPTION = typer.Option(
    6.0,
    "--chunk-seconds",
    help="连续监听单段最大秒数；尾部静音会更早收段。",
)
CHUNKS_CLI_OPTION = typer.Option(
    None,
    "--chunks",
    help="最多处理多少个分片；不传则持续监听直到 Ctrl+C。",
)
END_SILENCE_MS_CLI_OPTION = typer.Option(
    280,
    "--end-silence-ms",
    help="检测到人声后，尾部静音多少毫秒即收段。",
)
MIN_SPEECH_MS_CLI_OPTION = typer.Option(
    300,
    "--min-speech-ms",
    help="少于该时长的人声片段会被当作噪声丢弃。",
)
OVERLAP_SECONDS_CLI_OPTION = typer.Option(
    0.6,
    "--overlap-seconds",
    help="强制切段时带入下一段的音频重叠秒数，减少边界漏字。",
)
SPEECH_RMS_THRESHOLD_CLI_OPTION = typer.Option(
    180.0,
    "--speech-rms-threshold",
    help="判定有效人声的 RMS 阈值；环境噪声高时可调大。",
)
SHOW_LATENCY_CLI_OPTION = typer.Option(
    True,
    "--show-latency/--hide-latency",
    help="显示每段 ASR / 翻译播放耗时，便于调试延迟。",
)
REALTIME_PLAYBACK_QUEUE_SIZE = 1
REALTIME_MAX_PLAYBACK_SECONDS = 3.0
REALTIME_STALE_PLAYBACK_WAIT_SECONDS = 1.5


@app.command()
def start() -> None:
    """启动双向同传会话。"""
    DEFAULT_MANAGER.start()
    asyncio.run(DEFAULT_MANAGER.run_pipeline(direction=AudioDirection.UPLINK))
    asyncio.run(DEFAULT_MANAGER.run_pipeline(direction=AudioDirection.DOWNLINK))
    typer.echo("已启动 Teams 同传会话。")


@app.command()
def stop() -> None:
    """停止同传会话。"""
    DEFAULT_MANAGER.stop()
    typer.echo("已停止 Teams 同传会话。")


@app.command()
def pause() -> None:
    """暂停同传会话。"""
    DEFAULT_MANAGER.pause()
    typer.echo("已暂停 Teams 同传会话。")


@app.command()
def resume() -> None:
    """继续同传会话。"""
    DEFAULT_MANAGER.resume()
    typer.echo("已继续 Teams 同传会话。")


@app.command()
def status() -> None:
    """输出当前会话状态。"""
    typer.echo(json.dumps(DEFAULT_MANAGER.status_payload(), ensure_ascii=False, indent=2))


@app.command()
def say(
    text: str = typer.Argument(..., help="要翻译并播出的短句。"),
    target: str = typer.Option(
        "blackhole",
        "--target",
        help="发声目标：blackhole 写入 Teams 麦克风；default 写入本机默认输出。",
    ),
    direction_option: DirectionOption = DIRECTION_CLI_OPTION,
) -> None:
    """翻译一段短句并播到 BlackHole 或默认输出。"""
    try:
        direction = _direction_for_target(target, direction_option=direction_option)
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error
    try:
        result = asyncio.run(
            LiveSayBridge().say(
                text,
                direction=direction,
                target=target,
            )
        )
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error
    typer.echo(f"译文：{result.target_text}")
    typer.echo(f"已写入：{result.target_device_name} ({result.bytes_written} bytes)")


@app.command()
def ptt(
    seconds: float = typer.Option(
        3.0,
        "--seconds",
        help="每次录音秒数，例如 3 表示录 3 秒后识别并播出。",
    ),
    target: str = typer.Option(
        "blackhole",
        "--target",
        help="发声目标：blackhole 写入 Teams 麦克风；default 写入本机默认输出。",
    ),
    direction_option: DirectionOption = DIRECTION_CLI_OPTION,
) -> None:
    """录一段麦克风语音，识别后翻译并播出。"""
    try:
        direction = _direction_for_target(target, direction_option=direction_option)
        typer.echo("正在加载 Whisper 模型；加载完成后会开始录音。")
        bridge = _live_bridge_for_direction(direction)
        typer.echo(f"开始录音 {seconds:g} 秒，请现在说话。")
        samples = bridge.recorder.record(seconds=seconds)
        text = bridge.transcriber.transcribe(samples)
        typer.echo(f"识别：{text}")
        typer.echo("正在翻译并合成译音。")
        result = asyncio.run(
            bridge.say_bridge.say(
                text,
                direction=direction,
                target=target,
            )
        )
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error
    typer.echo(f"译文：{result.target_text}")
    typer.echo(f"已写入：{result.target_device_name} ({result.bytes_written} bytes)")


@app.command()
def listen(
    chunk_seconds: float = CHUNK_SECONDS_CLI_OPTION,
    chunks: int | None = CHUNKS_CLI_OPTION,
    end_silence_ms: int = END_SILENCE_MS_CLI_OPTION,
    min_speech_ms: int = MIN_SPEECH_MS_CLI_OPTION,
    overlap_seconds: float = OVERLAP_SECONDS_CLI_OPTION,
    speech_rms_threshold: float = SPEECH_RMS_THRESHOLD_CLI_OPTION,
    show_latency: bool = SHOW_LATENCY_CLI_OPTION,
    target: str = typer.Option(
        "blackhole",
        "--target",
        help="发声目标：blackhole 写入 Teams 麦克风；default 写入本机默认输出。",
    ),
    direction_option: DirectionOption = DIRECTION_CLI_OPTION,
) -> None:
    """连续监听默认麦克风，分片识别后翻译并播出。"""
    try:
        direction = _direction_for_target(target, direction_option=direction_option)
        typer.echo("正在加载 Whisper 模型；加载完成后会开始连续监听。")
        bridge = _live_bridge_for_direction(direction)
        warm_up_pyav_decoder()
        typer.echo("开始连续监听；按 Ctrl+C 停止。")
        _run_listen_pipeline(
            label="",
            recorder=StreamingMicrophoneRecorder(),
            bridge=bridge,
            direction=direction,
            target=target,
            chunk_seconds=chunk_seconds,
            chunks=chunks,
            end_silence_ms=end_silence_ms,
            min_speech_ms=min_speech_ms,
            overlap_seconds=overlap_seconds,
            speech_rms_threshold=speech_rms_threshold,
            show_latency=show_latency,
        )
    except KeyboardInterrupt:
        typer.echo("已停止连续监听。")
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error


@app.command()
def duplex(
    chunk_seconds: float = CHUNK_SECONDS_CLI_OPTION,
    chunks: int | None = CHUNKS_CLI_OPTION,
    end_silence_ms: int = END_SILENCE_MS_CLI_OPTION,
    min_speech_ms: int = MIN_SPEECH_MS_CLI_OPTION,
    overlap_seconds: float = OVERLAP_SECONDS_CLI_OPTION,
    speech_rms_threshold: float = SPEECH_RMS_THRESHOLD_CLI_OPTION,
    show_latency: bool = SHOW_LATENCY_CLI_OPTION,
    allow_shared_virtual_device: bool = typer.Option(
        False,
        "--allow-shared-virtual-device",
        help="仅用于临时测试：允许上行输出和下行输入使用同一个虚拟设备。",
    ),
) -> None:
    """启动真实双向同传：麦克风上行到 BlackHole，BlackHole 下行到默认输出。"""
    errors: queue.Queue[BaseException] = queue.Queue()
    route = _duplex_route(allow_shared_virtual_device=allow_shared_virtual_device)
    playback_gate: _PlaybackGate | None = _PlaybackGate() if route.shared_virtual_device else None
    typer.echo("正在加载两路 Whisper 模型；加载完成后会开始双向监听。")
    typer.echo(f"上行输出设备：{route.uplink_device.name}")
    typer.echo(f"下行输入设备：{route.downlink_device.name}")
    if route.shared_virtual_device:
        typer.echo(
            "提示：当前显式允许共享虚拟设备，只适合临时测试；正式会议请改成两路独立虚拟设备。"
        )
    warm_up_pyav_decoder()
    pipelines = [
        threading.Thread(
            target=_duplex_pipeline_runner,
            kwargs={
                "label": "上行",
                "recorder": StreamingMicrophoneRecorder(),
                "bridge": _live_bridge_for_direction(AudioDirection.UPLINK),
                "direction": AudioDirection.UPLINK,
                "target": "blackhole",
                "chunk_seconds": chunk_seconds,
                "chunks": chunks,
                "end_silence_ms": end_silence_ms,
                "min_speech_ms": min_speech_ms,
                "overlap_seconds": overlap_seconds,
                "speech_rms_threshold": speech_rms_threshold,
                "show_latency": show_latency,
                "errors": errors,
                "playback_gate": playback_gate,
                "suppress_downlink_on_playback": True,
            },
            daemon=True,
        ),
        threading.Thread(
            target=_duplex_pipeline_runner,
            kwargs={
                "label": "下行",
                "recorder": StreamingBlackHoleRecorder(device_name=route.downlink_device.name),
                "bridge": _live_bridge_for_direction(AudioDirection.DOWNLINK),
                "direction": AudioDirection.DOWNLINK,
                "target": "default",
                "chunk_seconds": chunk_seconds,
                "chunks": chunks,
                "end_silence_ms": end_silence_ms,
                "min_speech_ms": min_speech_ms,
                "overlap_seconds": overlap_seconds,
                "speech_rms_threshold": speech_rms_threshold,
                "show_latency": show_latency,
                "errors": errors,
                "playback_gate": playback_gate,
                "suppress_downlink_on_playback": False,
            },
            daemon=True,
        ),
    ]
    try:
        for pipeline in pipelines:
            pipeline.start()
        typer.echo("开始双向监听；按 Ctrl+C 停止。")
        while any(pipeline.is_alive() for pipeline in pipelines):
            try:
                error = errors.get_nowait()
            except queue.Empty:
                for pipeline in pipelines:
                    pipeline.join(timeout=0.1)
                continue
            raise error
    except KeyboardInterrupt:
        typer.echo("已停止双向监听。")
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error


@app.command()
def wizard(
    *,
    confirm_teams_route: bool = typer.Option(
        False,
        "--confirm-teams-route",
        help="确认 Teams 麦克风已选上行虚拟设备，扬声器已选下行虚拟设备。",
    ),
) -> None:
    """运行首次使用向导。"""
    typer.echo("首次使用向导：正在检查进入 Teams 前的阻断项。")
    settings = load_settings(validate_credentials=False)
    report = ReadinessChecker(
        deepseek_api_key_env=settings.deepseek_api_key_env,
        deepseek_api_key=settings.deepseek_api_key,
        teams_route_confirmed=confirm_teams_route,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.downlink_virtual_device_name,
        allow_shared_virtual_device=settings.allow_shared_virtual_device,
        vad_backend=settings.vad_backend,
        silero_vad_model_path=settings.silero_vad_model_path(),
        mode="phrase",
    ).run()
    _print_readiness_report(report)
    if not report.is_ready:
        raise typer.Exit(1)


@app.command()
def doctor(
    *,
    confirm_teams_route: bool = typer.Option(
        False,
        "--confirm-teams-route",
        help="确认 Teams 麦克风已选上行虚拟设备，扬声器已选下行虚拟设备。",
    ),
    deepseek_api_key_env: str = typer.Option(
        "DEEPSEEK_API_KEY",
        "--deepseek-api-key-env",
        help="DeepSeek API Key 所在环境变量名。",
    ),
    mode: str = typer.Option(
        "phrase",
        "--mode",
        help="检查模式：phrase=短句播入 Teams；realtime=实时麦克风同传。",
    ),
) -> None:
    """检查进入 Teams 会议前的阻断项。"""
    if mode not in {"phrase", "realtime"}:
        typer.echo("发生了什么：未知 doctor 模式。")
        typer.echo("下一步如何做：请使用 `--mode phrase` 或 `--mode realtime`。")
        raise typer.Exit(1)
    doctor_mode = cast(DoctorMode, mode)
    settings = load_settings(validate_credentials=False)
    report = ReadinessChecker(
        deepseek_api_key_env=deepseek_api_key_env,
        deepseek_api_key=settings.deepseek_api_key,
        teams_route_confirmed=confirm_teams_route,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.downlink_virtual_device_name,
        allow_shared_virtual_device=settings.allow_shared_virtual_device,
        vad_backend=settings.vad_backend,
        silero_vad_model_path=settings.silero_vad_model_path(),
        mode=doctor_mode,
    ).run()
    _print_readiness_report(report)
    if not report.is_ready:
        raise typer.Exit(1)


@app.command()
def serve(
    *,
    host: str = typer.Option("127.0.0.1", "--host", help="本地 Web 控制台绑定地址。"),
    port: int = typer.Option(8765, "--port", min=1024, max=65535, help="本地 Web 控制台端口。"),
) -> None:
    """启动本地 Web 控制台。"""
    uvicorn.run("teams_voice_interpreter.web.server:app", host=host, port=port)


def main() -> None:
    """运行命令行入口。"""
    app()


def _build_vad_backend() -> VadBackendProtocol:
    """按 settings.vad_backend 创建 VAD 后端实例；每个 pipeline 独立一份避免线程间 state 串扰。"""
    settings = load_settings(validate_credentials=False)
    if settings.vad_backend == "silero":
        return SileroBackend(model_path=settings.silero_vad_model_path())
    return WebRtcBackend()


class _PlaybackGate:
    """临时抑制下行，避免单 BlackHole 配置把上行译音重新识别成下行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._suppressed_until = 0.0

    def suppress_for(self, seconds: float) -> None:
        with self._lock:
            self._suppressed_until = max(self._suppressed_until, time.perf_counter() + seconds)

    def is_suppressed(self) -> bool:
        with self._lock:
            return time.perf_counter() < self._suppressed_until


@dataclass(frozen=True)
class _DuplexRoute:
    """双向同传的虚拟音频路由。"""

    uplink_device: AudioDevice
    downlink_device: AudioDevice
    shared_virtual_device: bool


def _duplex_route(*, allow_shared_virtual_device: bool) -> _DuplexRoute:
    settings = load_settings(validate_credentials=False)
    probe = AudioDeviceProbe()
    uplink_device = probe.find_output_device_by_name(
        settings.uplink_virtual_device_name,
        min_channels=2,
    )
    downlink_device = probe.find_input_device_by_name(
        settings.resolved_downlink_virtual_device_name(),
        min_channels=2,
    )
    shared_virtual_device = uplink_device.index == downlink_device.index
    shared_allowed = allow_shared_virtual_device or settings.allow_shared_virtual_device
    if shared_virtual_device and not shared_allowed:
        raise UserFacingError(
            code="duplex.shared_virtual_device",
            what_happened=(
                "发生了什么：上行输出和下行输入正在使用同一个虚拟音频设备，"
                "会把本机译音重新送回识别链路。"
            ),
            next_action=(
                "下一步如何做：请安装或创建第二个虚拟音频设备，并在 config.toml 中设置 "
                "`downlink_virtual_device_name`；临时测试才使用 "
                "`--allow-shared-virtual-device`。"
            ),
        )
    return _DuplexRoute(
        uplink_device=uplink_device,
        downlink_device=downlink_device,
        shared_virtual_device=shared_virtual_device,
    )


def _run_listen_pipeline(
    *,
    label: str,
    recorder: StreamingAudioRecorder,
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    chunk_seconds: float,
    chunks: int | None,
    end_silence_ms: int,
    min_speech_ms: int,
    overlap_seconds: float,
    speech_rms_threshold: float,
    show_latency: bool,
    playback_gate: _PlaybackGate | None = None,
    suppress_downlink_on_playback: bool = False,
) -> None:
    segment_queue: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue(maxsize=3)
    playback_queue: queue.Queue[_PendingPlayback | None] = queue.Queue(
        maxsize=REALTIME_PLAYBACK_QUEUE_SIZE
    )
    worker = threading.Thread(
        target=_listen_worker,
        args=(
            segment_queue,
            playback_queue,
            bridge,
            direction,
            target,
            label,
            show_latency,
            playback_gate,
        ),
        daemon=True,
    )
    playback_worker = threading.Thread(
        target=_listen_playback_worker,
        args=(playback_queue, bridge, playback_gate, suppress_downlink_on_playback),
        daemon=True,
    )
    worker.start()
    playback_worker.start()
    for index, samples in enumerate(
        recorder.segments(
            max_segment_seconds=chunk_seconds,
            end_silence_ms=end_silence_ms,
            min_speech_ms=min_speech_ms,
            overlap_seconds=overlap_seconds,
            rms_threshold=speech_rms_threshold,
            max_segments=chunks,
            vad_backend=_build_vad_backend(),
        ),
        start=1,
    ):
        segment_queue.put((index, samples))
    segment_queue.put(None)
    worker.join()
    playback_queue.put(None)
    playback_worker.join()


def _duplex_pipeline_runner(
    *,
    label: str,
    recorder: StreamingAudioRecorder,
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    chunk_seconds: float,
    chunks: int | None,
    end_silence_ms: int,
    min_speech_ms: int,
    overlap_seconds: float,
    speech_rms_threshold: float,
    show_latency: bool,
    errors: queue.Queue[BaseException],
    playback_gate: _PlaybackGate,
    suppress_downlink_on_playback: bool,
) -> None:
    try:
        _run_listen_pipeline(
            label=label,
            recorder=recorder,
            bridge=bridge,
            direction=direction,
            target=target,
            chunk_seconds=chunk_seconds,
            chunks=chunks,
            end_silence_ms=end_silence_ms,
            min_speech_ms=min_speech_ms,
            overlap_seconds=overlap_seconds,
            speech_rms_threshold=speech_rms_threshold,
            show_latency=show_latency,
            playback_gate=playback_gate,
            suppress_downlink_on_playback=suppress_downlink_on_playback,
        )
    except BaseException as error:
        errors.put(error)


def _listen_worker(
    segment_queue: queue.Queue[tuple[int, np.ndarray] | None],
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    label: str,
    show_latency: bool,
    playback_gate: _PlaybackGate | None,
) -> None:
    """后台处理 ASR / 翻译 / 合成，避免阻塞继续采集。"""
    loop = asyncio.new_event_loop()
    try:
        while True:
            item = segment_queue.get()
            try:
                if item is None:
                    return
                index, samples = item
                _prepare_listen_segment(
                    index=index,
                    samples=samples,
                    playback_queue=playback_queue,
                    bridge=bridge,
                    direction=direction,
                    target=target,
                    label=label,
                    show_latency=show_latency,
                    playback_gate=playback_gate,
                    loop=loop,
                )
            finally:
                segment_queue.task_done()
    finally:
        loop.close()


@dataclass(frozen=True)
class _PendingPlayback:
    """等待顺序播放的一段译音。"""

    index: int
    label: str
    prepared: PreparedSayResult
    started: float
    transcribed_at: float
    prepared_at: float
    queue_depth_at_enqueue: int
    dropped_pending_before_enqueue: int
    show_latency: bool


def _prepare_listen_segment(
    *,
    index: int,
    samples: np.ndarray,
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    label: str,
    show_latency: bool,
    playback_gate: _PlaybackGate | None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    started = time.perf_counter()
    display_index = _display_index(label, index)
    should_suppress_downlink = (
        direction is AudioDirection.DOWNLINK
        and playback_gate is not None
        and playback_gate.is_suppressed()
    )
    if should_suppress_downlink:
        typer.echo(f"{display_index} 已跳过：上行译音仍在写入 BlackHole，避免回灌。")
        return
    try:
        text = bridge.transcriber.transcribe(samples)
    except UserFacingError as error:
        typer.echo(f"{display_index} {error.what_happened}")
        return
    transcribed_at = time.perf_counter()
    typer.echo(f"{display_index} 识别：{text}")
    try:
        prepare_coro = bridge.say_bridge.prepare(
            text,
            direction=direction,
            target=target,
            streaming=True,
        )
        prepared = (
            loop.run_until_complete(prepare_coro)
            if loop is not None
            else asyncio.run(prepare_coro)
        )
    except UserFacingError as error:
        typer.echo(f"{display_index} {error}")
        return
    prepared_at = time.perf_counter()
    typer.echo(f"{display_index} 译文：{prepared.target_text}")
    dropped_pending = _drop_pending_playbacks(playback_queue)
    if dropped_pending:
        typer.echo(
            f"{display_index} 已丢弃 {dropped_pending} 个未播放旧段：实时播放只保留最新译音。"
        )
    queue_depth_at_enqueue = playback_queue.qsize()
    playback_queue.put(
        _PendingPlayback(
            index=index,
            label=label,
            prepared=prepared,
            started=started,
            transcribed_at=transcribed_at,
            prepared_at=prepared_at,
            queue_depth_at_enqueue=queue_depth_at_enqueue,
            dropped_pending_before_enqueue=dropped_pending,
            show_latency=show_latency,
        )
    )


def _drop_pending_playbacks(playback_queue: queue.Queue[_PendingPlayback | None]) -> int:
    """丢弃尚未开始播放的旧译音，实时模式只保留最新段。"""
    dropped = 0
    while True:
        try:
            item = playback_queue.get_nowait()
        except queue.Empty:
            return dropped
        if item is None:
            playback_queue.put(None)
            return dropped
        playback_queue.task_done()
        dropped += 1


def _listen_playback_worker(
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    playback_gate: _PlaybackGate | None = None,
    suppress_downlink_on_playback: bool = False,
) -> None:
    """按顺序播放已准备好的译音。"""
    while True:
        item = playback_queue.get()
        try:
            if item is None:
                return
            stale_wait_s = time.perf_counter() - item.prepared_at
            if stale_wait_s > REALTIME_STALE_PLAYBACK_WAIT_SECONDS:
                typer.echo(
                    f"{_display_index(item.label, item.index)} 已丢弃：译音等待播放 "
                    f"{stale_wait_s:.2f}s，超过实时窗口 "
                    f"{REALTIME_STALE_PLAYBACK_WAIT_SECONDS:.2f}s。"
                )
                continue
            if suppress_downlink_on_playback and playback_gate is not None:
                playback_gate.suppress_for(
                    min(_prepared_audio_seconds(item.prepared), REALTIME_MAX_PLAYBACK_SECONDS) + 0.8
                )
            playback_started = time.perf_counter()
            try:
                result = asyncio.run(
                    _play_prepared_for_listen(
                        bridge,
                        item.prepared,
                        max_playback_seconds=REALTIME_MAX_PLAYBACK_SECONDS,
                    )
                )
            except UserFacingError as error:
                typer.echo(f"{_display_index(item.label, item.index)} {error}")
                continue
            completed_at = time.perf_counter()
            typer.echo(
                f"{_display_index(item.label, item.index)} "
                f"已写入：{result.target_device_name} ({result.bytes_written} bytes)"
            )
            _print_listen_latency(
                item,
                result=result,
                playback_started=playback_started,
                completed_at=completed_at,
            )
        finally:
            playback_queue.task_done()


async def _play_prepared_for_listen(
    bridge: LivePushToTalkBridge,
    prepared: PreparedSayResult,
    *,
    max_playback_seconds: float | None = None,
) -> SayResult:
    play_streaming = cast(
        Callable[..., Awaitable[SayResult]] | None,
        getattr(bridge.say_bridge, "play_prepared_streaming", None),
    )
    if play_streaming is None:
        return await asyncio.to_thread(bridge.say_bridge.play_prepared, prepared)
    return await play_streaming(prepared, max_playback_seconds=max_playback_seconds)


def _print_listen_latency(
    item: _PendingPlayback,
    *,
    result: SayResult,
    playback_started: float,
    completed_at: float,
) -> None:
    if not item.show_latency:
        return
    prepare_wall_s = item.prepared_at - item.transcribed_at
    queue_wait_s = playback_started - item.prepared_at
    first_pcm_s = playback_started + result.first_pcm_latency_s - item.transcribed_at
    first_write_s = _first_write_latency_from_transcription(
        item,
        result=result,
        playback_started=playback_started,
    )
    first_byte_s = first_write_s if first_write_s is not None else first_pcm_s
    typer.echo(
        f"{_display_index(item.label, item.index)} "
        f"耗时：ASR {item.transcribed_at - item.started:.2f}s / "
        f"MT首T {result.mt_first_token_latency_s:.2f}s / "
        f"MT总 {result.translation_latency_s:.2f}s / prepare墙钟 {prepare_wall_s:.2f}s / "
        f"TTS {result.tts_latency_s:.2f}s / 解码 {result.decode_latency_s:.2f}s / "
        f"排队 {queue_wait_s:.2f}s"
        f"(q={item.queue_depth_at_enqueue},drop={item.dropped_pending_before_enqueue}) / "
        f"首PCM {first_pcm_s:.2f}s / 首写 {_format_optional_seconds(first_write_s)} / "
        f"首字节 {first_byte_s:.2f}s / "
        f"播放 {result.playback_latency_s:.2f}s{_format_truncated(result)} / "
        f"总计 {completed_at - item.started:.2f}s"
    )


def _first_write_latency_from_transcription(
    item: _PendingPlayback,
    *,
    result: SayResult,
    playback_started: float,
) -> float | None:
    if result.first_playback_write_latency_s is None:
        return None
    return playback_started + result.first_playback_write_latency_s - item.transcribed_at


def _format_optional_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}s"


def _format_truncated(result: SayResult) -> str:
    if not result.playback_truncated:
        return ""
    return f"(截断≤{REALTIME_MAX_PLAYBACK_SECONDS:.1f}s)"


def _display_index(label: str, index: int) -> str:
    if not label:
        return f"[{index}]"
    return f"[{label} {index}]"


def _prepared_audio_seconds(prepared: PreparedSayResult) -> float:
    if prepared.pcm.size > 0:
        return float(prepared.pcm.size) / 16000
    return min(10.0, max(1.0, len(prepared.target_text) / 8.0))


def _live_bridge_for_direction(direction: AudioDirection) -> LivePushToTalkBridge:
    return LivePushToTalkBridge(source_language=_source_language_for_direction(direction))


def _source_language_for_direction(direction: AudioDirection) -> str:
    if direction is AudioDirection.UPLINK:
        return "zh"
    return "en"


def _print_readiness_report(report: ReadinessReport) -> None:
    heading = "已就绪：可以进入 Teams 测试通话。" if report.is_ready else "未就绪：请先处理阻断项。"
    typer.echo(heading)
    for check in report.checks:
        mark = "OK" if check.status is CheckStatus.PASS else "FAIL"
        typer.echo(f"[{mark}] {check.title}: {check.detail}")
        if check.status is CheckStatus.FAIL and check.next_action:
            typer.echo(f"      {check.next_action}")


def _direction_for_target(
    target: str,
    *,
    direction_option: DirectionOption = "auto",
) -> AudioDirection:
    if direction_option == "uplink":
        return AudioDirection.UPLINK
    if direction_option == "downlink":
        return AudioDirection.DOWNLINK
    if target == "blackhole":
        return AudioDirection.UPLINK
    if target == "default":
        return AudioDirection.DOWNLINK
    raise UserFacingError(
        code="cli.target_invalid",
        what_happened=f"发生了什么：未知发声目标 `{target}`。",
        next_action="下一步如何做：请使用 `--target blackhole` 或 `--target default`。",
    )
