"""Typer CLI 入口。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import queue
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar, cast

import numpy as np
import typer
import uvicorn

from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.config import load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import StableTranscriptChunk, TranscriptKind
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_ptt import (
    LivePushToTalkBridge,
    SpeechSegment,
    StreamingAudioRecorder,
    StreamingBlackHoleRecorder,
    StreamingMicrophoneRecorder,
    _is_speech_frame,
    _StableSpeechSegmenter,
    _update_forced_split_continuation_on_open_frame,
)
from teams_voice_interpreter.live_say import LiveSayBridge, PreparedSayResult, SayResult
from teams_voice_interpreter.readiness import (
    CheckStatus,
    LowLatencyProof,
    ReadinessChecker,
    ReadinessReport,
)
from teams_voice_interpreter.session.manager import DEFAULT_MANAGER
from teams_voice_interpreter.session.transcript_ledger import TranscriptLedger
from teams_voice_interpreter.stt.vad import (
    SileroBackend,
    VadBackendProtocol,
    VadSegmenter,
    WebRtcBackend,
)
from teams_voice_interpreter.stt.whisper_streaming import OnlineASRProcessor
from teams_voice_interpreter.tts.audio_decode import warm_up_pyav_decoder

app = typer.Typer(help="Teams 双向实时语音同传桥")
DoctorMode = Literal["phrase", "realtime"]
DirectionOption = Literal["auto", "uplink", "downlink"]
_T = TypeVar("_T")
LOW_LATENCY_PROOF_CLI_OPTION = typer.Option(
    None,
    "--low-latency-proof",
    help="读取 scripts/probe_online_asr.py --proof-json 生成的低延迟验收 proof。",
)
UPLINK_LOW_LATENCY_PROOF_CLI_OPTION = typer.Option(
    None,
    "--uplink-low-latency-proof",
    help="duplex 上行 early-prepare 使用的低延迟 proof。",
)
DOWNLINK_LOW_LATENCY_PROOF_CLI_OPTION = typer.Option(
    None,
    "--downlink-low-latency-proof",
    help="duplex 下行 early-prepare 使用的低延迟 proof。",
)
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
    150,
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
ONLINE_ASR_CLI_OPTION = typer.Option(
    False,
    "--online-asr/--segment-asr",
    help="实验模式：持续喂音频 chunk 产生稳定 partial；默认仍使用整段 ASR。",
)
ONLINE_ASR_EARLY_PREPARE_CLI_OPTION = typer.Option(
    False,
    "--online-asr-early-prepare/--no-online-asr-early-prepare",
    help="更激进的实验：允许 online-asr stable partial 提前调用 MT/TTS；默认关闭。",
)
INPUT_DEVICE_NAME_CLI_OPTION = typer.Option(
    "",
    "--input-device-name",
    help="真实麦克风设备名称；不传则使用 macOS 默认输入。可先运行 `tvi devices` 查看。",
)
REALTIME_PLAYBACK_QUEUE_SIZE = 0
REALTIME_PLAYBACK_BACKLOG_WARNING_DEPTH = 3
REALTIME_PLAYBACK_OLD_BURST_DROP_DEPTH = 8
REALTIME_EARLY_STABLE_CHUNK_PREPARE = True
REALTIME_EARLY_STABLE_MIN_CJK_CHARS = 10
REALTIME_EARLY_STABLE_MIN_WORDS = 5
REALTIME_MAX_PLAYBACK_SECONDS: float | None = None
REALTIME_STALE_PLAYBACK_WAIT_SECONDS: float | None = None
TRANSLATION_UNIT_BOUNDARY_CHARS = set(" \t\n\r,.!?;:，。！？；：、")
MIN_CJK_OVERLAP_CHARS = 3
MIN_WORD_OVERLAP = 2


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
def devices() -> None:
    """列出当前 CoreAudio 输入 / 输出设备，辅助修复 doctor 阻断项。"""
    settings = load_settings(validate_credentials=False)
    probe = AudioDeviceProbe()
    default_input = _safe_default_device(probe.get_default_input)
    default_output = _safe_default_device(probe.get_default_output)
    input_devices = probe.input_devices()
    output_devices = probe.output_devices()
    typer.echo("输入设备：")
    _print_audio_devices(
        input_devices,
        default_device=default_input,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.resolved_downlink_virtual_device_name(),
    )
    typer.echo("输出设备：")
    _print_audio_devices(
        output_devices,
        default_device=default_output,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.resolved_downlink_virtual_device_name(),
    )
    _print_physical_candidates(
        "真实输入候选",
        input_devices,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.resolved_downlink_virtual_device_name(),
    )
    _print_physical_candidates(
        "真实输出候选",
        output_devices,
        uplink_virtual_device_name=settings.uplink_virtual_device_name,
        downlink_virtual_device_name=settings.resolved_downlink_virtual_device_name(),
    )


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
    online_asr: bool = ONLINE_ASR_CLI_OPTION,
    online_asr_early_prepare: bool = ONLINE_ASR_EARLY_PREPARE_CLI_OPTION,
    low_latency_proof: Path | None = LOW_LATENCY_PROOF_CLI_OPTION,
    input_device_name: str = INPUT_DEVICE_NAME_CLI_OPTION,
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
        _validate_online_asr_options(
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
            low_latency_proof=low_latency_proof,
            expected_direction=direction,
            expected_language=_source_language_for_direction(direction),
        )
        input_device = _microphone_input_device(input_device_name=input_device_name)
        typer.echo("正在加载 Whisper 模型；加载完成后会开始连续监听。")
        typer.echo(f"真实输入设备：{input_device.name}")
        bridge = _live_bridge_for_direction(direction)
        warm_up_pyav_decoder()
        _warn_online_asr_early_prepare_if_disabled(
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
        )
        ledger = TranscriptLedger.create_default()
        typer.echo(f"Transcript ledger：{ledger.path}")
        typer.echo("开始连续监听；按 Ctrl+C 停止。")
        _run_listen_pipeline(
            label="",
            recorder=StreamingMicrophoneRecorder(device_name=input_device_name),
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
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
            ledger=ledger,
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
    online_asr: bool = ONLINE_ASR_CLI_OPTION,
    online_asr_early_prepare: bool = ONLINE_ASR_EARLY_PREPARE_CLI_OPTION,
    uplink_low_latency_proof: Path | None = UPLINK_LOW_LATENCY_PROOF_CLI_OPTION,
    downlink_low_latency_proof: Path | None = DOWNLINK_LOW_LATENCY_PROOF_CLI_OPTION,
    input_device_name: str = INPUT_DEVICE_NAME_CLI_OPTION,
    allow_shared_virtual_device: bool = typer.Option(
        False,
        "--allow-shared-virtual-device",
        help="仅用于临时测试：允许上行输出和下行输入使用同一个虚拟设备。",
    ),
) -> None:
    """启动真实双向同传：麦克风上行到 BlackHole，BlackHole 下行到默认输出。"""
    try:
        _validate_online_asr_options(
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
            low_latency_proof=uplink_low_latency_proof,
            expected_direction=AudioDirection.UPLINK,
            expected_language=_source_language_for_direction(AudioDirection.UPLINK),
            proof_label="上行",
        )
        _validate_online_asr_options(
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
            low_latency_proof=downlink_low_latency_proof,
            expected_direction=AudioDirection.DOWNLINK,
            expected_language=_source_language_for_direction(AudioDirection.DOWNLINK),
            proof_label="下行",
        )
        route = _duplex_route(allow_shared_virtual_device=allow_shared_virtual_device)
        input_device = _microphone_input_device(input_device_name=input_device_name)
        output_device = _default_output_device()
    except UserFacingError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error
    errors: queue.Queue[BaseException] = queue.Queue()
    playback_gate: _PlaybackGate | None = _PlaybackGate() if route.shared_virtual_device else None
    typer.echo("正在加载两路 Whisper 模型；加载完成后会开始双向监听。")
    typer.echo(f"上行真实输入设备：{input_device.name}")
    typer.echo(f"上行输出设备：{route.uplink_device.name}")
    typer.echo(f"下行输入设备：{route.downlink_device.name}")
    typer.echo(f"下行真实输出设备：{output_device.name}")
    ledger = TranscriptLedger.create_default()
    typer.echo(f"Transcript ledger：{ledger.path}")
    if route.shared_virtual_device:
        typer.echo(
            "提示：当前显式允许共享虚拟设备，只适合临时测试；正式会议请改成两路独立虚拟设备。"
        )
    _warn_online_asr_early_prepare_if_disabled(
        online_asr=online_asr,
        online_asr_early_prepare=online_asr_early_prepare,
    )
    warm_up_pyav_decoder()
    pipelines = [
        threading.Thread(
            target=_duplex_pipeline_runner,
            kwargs={
                "label": "上行",
                "recorder": StreamingMicrophoneRecorder(device_name=input_device_name),
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
                "online_asr": online_asr,
                "online_asr_early_prepare": online_asr_early_prepare,
                "errors": errors,
                "playback_gate": playback_gate,
                "suppress_downlink_on_playback": True,
                "ledger": ledger,
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
                "online_asr": online_asr,
                "online_asr_early_prepare": online_asr_early_prepare,
                "errors": errors,
                "playback_gate": playback_gate,
                "suppress_downlink_on_playback": False,
                "ledger": ledger,
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
                pipeline_error = errors.get_nowait()
            except queue.Empty:
                for pipeline in pipelines:
                    pipeline.join(timeout=0.1)
                continue
            raise pipeline_error
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
        tts_engine=settings.tts_engine,
        piper_models_dir=settings.resolved_piper_models_dir(),
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
    require_low_latency: bool = typer.Option(
        False,
        "--require-low-latency/--no-require-low-latency",
        help="把低延迟验收作为阻断门禁；当前未接入 true streaming ASR 时会 fail-closed。",
    ),
    low_latency_proof: Path | None = LOW_LATENCY_PROOF_CLI_OPTION,
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
        tts_engine=settings.tts_engine,
        piper_models_dir=settings.resolved_piper_models_dir(),
        mode=doctor_mode,
        require_low_latency=require_low_latency,
        low_latency_proof=_read_low_latency_proof(low_latency_proof),
    ).run()
    _print_readiness_report(report)
    if not report.is_ready:
        raise typer.Exit(1)


def _read_low_latency_proof(
    path: Path | None,
    *,
    expected_direction: AudioDirection | None = None,
    expected_language: str | None = None,
) -> LowLatencyProof | None:
    """读取并复核 online-ASR proof JSON。"""
    if path is None:
        return None
    payload = _load_low_latency_proof_payload(path)
    if isinstance(payload, LowLatencyProof):
        return payload
    return _validate_low_latency_proof_payload(
        payload,
        expected_direction=expected_direction,
        expected_language=expected_language,
    )


def _load_low_latency_proof_payload(path: Path) -> dict[str, object] | LowLatencyProof:
    """读取 online-ASR proof JSON payload。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return LowLatencyProof(
            verified=False,
            detail=f"无法读取低延迟 proof：{path} ({error})",
            next_action=(
                "下一步如何做：请先运行 `scripts/probe_online_asr.py --proof-json <path>` "
                "生成 proof，再重新执行 doctor。"
            ),
        )
    except json.JSONDecodeError as error:
        return LowLatencyProof(
            verified=False,
            detail=f"低延迟 proof 不是合法 JSON：{path} ({error.msg})",
            next_action="下一步如何做：请重新运行 online ASR 探针生成 proof JSON。",
        )
    if not isinstance(raw, dict):
        return LowLatencyProof(
            verified=False,
            detail="低延迟 proof 格式错误：顶层必须是 JSON object。",
            next_action="下一步如何做：请重新运行 online ASR 探针生成 proof JSON。",
        )
    return cast("dict[str, object]", raw)


def _validate_low_latency_proof_payload(
    proof: dict[str, object],
    *,
    expected_direction: AudioDirection | None = None,
    expected_language: str | None = None,
) -> LowLatencyProof:
    """复核 online-ASR proof JSON 指标与阈值。"""
    metrics = _json_object(proof.get("metrics"))
    thresholds = _json_object(proof.get("thresholds"))
    scope = _json_object(proof.get("scope"))
    failures = _json_string_list(proof.get("failures"))
    first_confirmed_ready = _json_number(metrics, "first_confirmed_ready_partial_s")
    cer = _json_number(metrics, "cer")
    max_first_partial = _json_number(thresholds, "max_first_partial_s")
    max_cer = _json_number(thresholds, "max_cer")
    problems = _low_latency_proof_problems(
        passed=proof.get("passed") is True,
        first_confirmed_ready=first_confirmed_ready,
        max_first_partial=max_first_partial,
        cer=cer,
        max_cer=max_cer,
    )
    problems.extend(
        _low_latency_scope_problems(
            scope,
            expected_direction=expected_direction,
            expected_language=expected_language,
        )
    )
    problems.extend(failures)
    if problems:
        return LowLatencyProof(
            verified=False,
            detail="低延迟 proof 未通过：" + "；".join(dict.fromkeys(problems)),
            next_action=(
                "下一步如何做：请重新运行 online ASR 探针，确认低延迟阈值和 CER 阈值同时通过。"
            ),
        )
    return LowLatencyProof(
        verified=True,
        detail=(
            "低延迟 proof 通过：首个 final 可确认可翻译 stable partial "
            f"{first_confirmed_ready:.2f}s <= {max_first_partial:.2f}s，"
            f"CER {cer:.3f} <= {max_cer:.3f}。"
        ),
    )


def _low_latency_proof_problems(
    *,
    passed: bool,
    first_confirmed_ready: float | None,
    max_first_partial: float | None,
    cer: float | None,
    max_cer: float | None,
) -> list[str]:
    problems: list[str] = []
    if not passed:
        problems.append("proof 标记为未通过")
    if max_first_partial is None:
        problems.append("缺少 thresholds.max_first_partial_s")
    if max_cer is None:
        problems.append("缺少 thresholds.max_cer")
    if first_confirmed_ready is None:
        problems.append("缺少 metrics.first_confirmed_ready_partial_s")
    if cer is None:
        problems.append("缺少 metrics.cer")
    if first_confirmed_ready is not None and max_first_partial is not None:
        if first_confirmed_ready > max_first_partial:
            problems.append(
                f"首个 final 可确认可翻译 stable partial {first_confirmed_ready:.2f}s "
                f"> {max_first_partial:.2f}s"
            )
    if cer is not None and max_cer is not None and cer > max_cer:
        problems.append(f"CER {cer:.3f} > {max_cer:.3f}")
    return problems


def _low_latency_scope_problems(
    scope: dict[str, object],
    *,
    expected_direction: AudioDirection | None,
    expected_language: str | None,
) -> list[str]:
    problems: list[str] = []
    direction = _json_string(scope, "direction")
    language = _json_string(scope, "language")
    if expected_direction is not None and direction != expected_direction.value:
        problems.append(f"proof 方向 `{direction or 'missing'}` != `{expected_direction.value}`")
    if expected_language is not None and language != expected_language:
        problems.append(f"proof 语言 `{language or 'missing'}` != `{expected_language}`")
    return problems


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    return {}


def _json_number(mapping: dict[str, object], key: str) -> float | None:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _json_string(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if not isinstance(value, str):
        return None
    return value


def _json_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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


def _validate_online_asr_options(
    *,
    online_asr: bool,
    online_asr_early_prepare: bool,
    low_latency_proof: Path | None = None,
    expected_direction: AudioDirection | None = None,
    expected_language: str | None = None,
    proof_label: str = "",
) -> None:
    if online_asr_early_prepare and not online_asr:
        raise UserFacingError(
            code="online_asr.early_prepare_requires_online_asr",
            what_happened=(
                "发生了什么：`--online-asr-early-prepare` 只对 online ASR 实验路径生效，"
                "当前没有启用 `--online-asr`。"
            ),
            next_action=(
                "下一步如何做：必须同时启用 `--online-asr`，或删除 "
                "`--online-asr-early-prepare` 使用默认整段 ASR。"
            ),
        )
    if not online_asr_early_prepare:
        return
    if low_latency_proof is None:
        label = f"{proof_label} " if proof_label else ""
        raise UserFacingError(
            code="online_asr.early_prepare_requires_low_latency_proof",
            what_happened=(
                "发生了什么：`--online-asr-early-prepare` 会让 stable partial "
                f"提前调用 MT/TTS，但当前没有提供已通过的{label}低延迟 proof。"
            ),
            next_action=(
                "下一步如何做：请先运行 `scripts/probe_online_asr.py --proof-json <path>`，"
                "再加上对应的 proof 参数；或删除 `--online-asr-early-prepare`。"
            ),
        )
    proof = _read_low_latency_proof(
        low_latency_proof,
        expected_direction=expected_direction,
        expected_language=expected_language,
    )
    if proof is None or proof.verified:
        return
    raise UserFacingError(
        code="online_asr.early_prepare_low_latency_proof_failed",
        what_happened=f"发生了什么：{proof.detail}",
        next_action=proof.next_action,
    )


def _build_vad_backend() -> VadBackendProtocol:
    """按 settings.vad_backend 创建 VAD 后端实例；每个 pipeline 独立一份避免线程间 state 串扰。"""
    settings = load_settings(validate_credentials=False)
    if settings.vad_backend == "silero":
        return SileroBackend(model_path=settings.silero_vad_model_path())
    return WebRtcBackend()


def _warn_online_asr_early_prepare_if_disabled(
    *,
    online_asr: bool,
    online_asr_early_prepare: bool,
) -> None:
    if online_asr and not online_asr_early_prepare:
        typer.echo(
            "提示：--online-asr 默认不让 stable partial 提前调用 MT/TTS；"
            "请先用 scripts/probe_online_asr.py 确认 final 可确认的 partial 达标，"
            "再显式启用 --online-asr-early-prepare。"
        )


def _close_live_bridge(bridge: LivePushToTalkBridge) -> None:
    """关闭真实 bridge 的后台资源；测试 fake bridge 没有 close 时跳过。"""
    close = getattr(bridge, "close", None)
    if callable(close):
        close()


class _PlaybackGate:
    """临时抑制下行，避免单 BlackHole 配置把上行译音重新识别成下行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._suppressed_until = 0.0
        self._active_playbacks = 0

    def suppress_for(self, seconds: float) -> float:
        with self._lock:
            deadline = time.perf_counter() + seconds
            self._suppressed_until = max(self._suppressed_until, deadline)
            return self._suppressed_until

    def begin_playback_suppression(self, *, hard_cap_seconds: float) -> None:
        """播放期间强制抑制；hard cap 只兜底异常退出未释放的情况。"""
        with self._lock:
            self._active_playbacks += 1
            self._suppressed_until = max(
                self._suppressed_until,
                time.perf_counter() + hard_cap_seconds,
            )

    def finish_playback_suppression(self, *, tail_seconds: float) -> None:
        """播放结束后释放 active 状态，仅保留尾部缓冲。"""
        with self._lock:
            self._active_playbacks = max(0, self._active_playbacks - 1)
            if self._active_playbacks == 0:
                self._suppressed_until = time.perf_counter() + tail_seconds

    def is_suppressed(self) -> bool:
        with self._lock:
            return self._active_playbacks > 0 or time.perf_counter() < self._suppressed_until


class _AsyncLoopRunner:
    """在后台事件循环上运行 MT/TTS prepare，允许 ASR callback 提前提交任务。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def submit(self, coro: Coroutine[object, object, _T]) -> concurrent.futures.Future[_T]:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[object, object, _T]) -> _T:
        return self.submit(coro).result()

    def close(self) -> None:
        cleanup = asyncio.run_coroutine_threadsafe(self._cancel_pending_tasks(), self._loop)
        cleanup.result(timeout=1.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _cancel_pending_tasks(self) -> None:
        current = asyncio.current_task(loop=self._loop)
        pending = [
            task
            for task in asyncio.all_tasks(loop=self._loop)
            if task is not current and not task.done()
        ]
        if not pending:
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


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
    online_asr: bool = False,
    online_asr_early_prepare: bool = False,
    playback_gate: _PlaybackGate | None = None,
    suppress_downlink_on_playback: bool = False,
    ledger: TranscriptLedger | None = None,
) -> None:
    if online_asr:
        _run_online_listen_pipeline(
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
            online_asr_early_prepare=online_asr_early_prepare,
            playback_gate=playback_gate,
            suppress_downlink_on_playback=suppress_downlink_on_playback,
            ledger=ledger,
        )
        return
    segment_queue: queue.Queue[tuple[int, SpeechSegment] | None] = queue.Queue(maxsize=3)
    playback_queue: queue.Queue[_PendingPlayback | None] = queue.Queue(
        maxsize=REALTIME_PLAYBACK_QUEUE_SIZE
    )
    burst_tracker = _BurstTracker()
    transcript_deduplicator = _TranscriptOverlapDeduplicator()
    async_runner = _AsyncLoopRunner()
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
            burst_tracker,
            transcript_deduplicator,
            async_runner,
            ledger,
        ),
        daemon=True,
    )
    playback_worker = threading.Thread(
        target=_listen_playback_worker,
        args=(playback_queue, bridge, playback_gate, suppress_downlink_on_playback),
        daemon=True,
    )
    worker_started = False
    playback_worker_started = False
    segment_stop_sent = False
    playback_stop_sent = False
    try:
        worker.start()
        worker_started = True
        playback_worker.start()
        playback_worker_started = True
        for index, segment in enumerate(
            _recorder_speech_segments(
                recorder,
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
            segment_queue.put((index, segment))
        segment_queue.put(None)
        segment_stop_sent = True
        worker.join()
        playback_queue.put(None)
        playback_stop_sent = True
        playback_worker.join()
    finally:
        if worker_started:
            if not segment_stop_sent:
                segment_queue.put(None)
            worker.join()
        if playback_worker_started:
            if not playback_stop_sent:
                playback_queue.put(None)
            playback_worker.join()
        _close_live_bridge(bridge)
        async_runner.close()


def _recorder_speech_segments(
    recorder: StreamingAudioRecorder,
    *,
    max_segment_seconds: float,
    end_silence_ms: int,
    min_speech_ms: int,
    overlap_seconds: float,
    rms_threshold: float,
    max_segments: int | None,
    vad_backend: VadBackendProtocol,
) -> Iterable[SpeechSegment]:
    speech_segments = getattr(recorder, "speech_segments", None)
    if callable(speech_segments):
        yield from speech_segments(
            max_segment_seconds=max_segment_seconds,
            end_silence_ms=end_silence_ms,
            min_speech_ms=min_speech_ms,
            overlap_seconds=overlap_seconds,
            rms_threshold=rms_threshold,
            max_segments=max_segments,
            vad_backend=vad_backend,
        )
        return
    for samples in recorder.segments(
        max_segment_seconds=max_segment_seconds,
        end_silence_ms=end_silence_ms,
        min_speech_ms=min_speech_ms,
        overlap_seconds=overlap_seconds,
        rms_threshold=rms_threshold,
        max_segments=max_segments,
        vad_backend=vad_backend,
    ):
        yield SpeechSegment(samples=samples, continues_previous=False, closed_by="silence")


def _run_online_listen_pipeline(
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
    online_asr_early_prepare: bool,
    playback_gate: _PlaybackGate | None = None,
    suppress_downlink_on_playback: bool = False,
    ledger: TranscriptLedger | None = None,
) -> None:
    """实验性在线 ASR 管线：帧级喂 ASR partial，VAD 收段时 final 收口。"""
    playback_queue: queue.Queue[_PendingPlayback | None] = queue.Queue(
        maxsize=REALTIME_PLAYBACK_QUEUE_SIZE
    )
    burst_tracker = _BurstTracker()
    transcript_deduplicator = _TranscriptOverlapDeduplicator()
    async_runner = _AsyncLoopRunner()
    playback_worker = threading.Thread(
        target=_listen_playback_worker,
        args=(playback_queue, bridge, playback_gate, suppress_downlink_on_playback),
        daemon=True,
    )
    vad_backend = _build_vad_backend()
    actual_frame_ms = round(vad_backend.frame_samples * 1000 / vad_backend.sample_rate_hz)
    segmenter = _StableSpeechSegmenter(
        overlap_frames=max(0, int(overlap_seconds * 1000 / actual_frame_ms)),
        end_silence_frames=max(1, end_silence_ms // actual_frame_ms),
        min_speech_frames=max(1, min_speech_ms // actual_frame_ms),
        max_segment_frames=max(1, int(chunk_seconds * 1000 / actual_frame_ms)),
    )
    vad = VadSegmenter(backend=vad_backend, sample_rate_hz=recorder.sample_rate_hz)
    processor = _new_online_asr_processor(direction=direction, bridge=bridge)
    early_preparer = _new_early_preparer(
        bridge=bridge,
        direction=direction,
        target=target,
        async_runner=async_runner,
        enable_partial_prepare=online_asr_early_prepare,
    )
    emitted = 0
    previous_closed_by_max_length = False
    boundary_silence_frames = 0
    current_segment_started_at: float | None = None
    try:
        playback_worker.start()
        for frame in recorder.chunks(chunk_seconds=actual_frame_ms / 1000):
            frame_received_at = time.perf_counter()
            is_speech = _is_speech_frame(frame, vad=vad, rms_threshold=speech_rms_threshold)
            if is_speech and current_segment_started_at is None:
                current_segment_started_at = frame_received_at - (
                    len(segmenter.pre_roll) * actual_frame_ms / 1000
                )
            if is_speech:
                _accept_online_partials(processor, frame=frame, early_preparer=early_preparer)
            segment = segmenter.accept(frame, is_speech=is_speech)
            if segment is None:
                if current_segment_started_at is not None and not segmenter.segment_frames:
                    current_segment_started_at = None
                was_continuing_forced_split = previous_closed_by_max_length
                previous_closed_by_max_length, boundary_silence_frames = (
                    _update_forced_split_continuation_on_open_frame(
                        segmenter=segmenter,
                        previous_closed_by_max_length=previous_closed_by_max_length,
                        boundary_silence_frames=boundary_silence_frames,
                        is_speech=is_speech,
                    )
                )
                if was_continuing_forced_split and not previous_closed_by_max_length:
                    early_preparer = _new_early_preparer(
                        bridge=bridge,
                        direction=direction,
                        target=target,
                        async_runner=async_runner,
                        enable_partial_prepare=online_asr_early_prepare,
                    )
                continue
            emitted += 1
            close_reason = segmenter.last_close_reason or "silence"
            _prepare_online_final_segment(
                index=emitted,
                samples=segment,
                continues_previous=previous_closed_by_max_length,
                segment_started_at=current_segment_started_at,
                processor=processor,
                early_preparer=early_preparer,
                playback_queue=playback_queue,
                bridge=bridge,
                direction=direction,
                target=target,
                label=label,
                show_latency=show_latency,
                playback_gate=playback_gate,
                async_runner=async_runner,
                burst_tracker=burst_tracker,
                transcript_deduplicator=transcript_deduplicator,
                ledger=ledger,
            )
            previous_closed_by_max_length = close_reason == "max_length"
            boundary_silence_frames = 0
            current_segment_started_at = None
            processor = _new_online_asr_processor(direction=direction, bridge=bridge)
            next_context = (
                transcript_deduplicator.committed_text if previous_closed_by_max_length else ""
            )
            early_preparer = _new_early_preparer(
                bridge=bridge,
                direction=direction,
                target=target,
                async_runner=async_runner,
                enable_partial_prepare=online_asr_early_prepare,
                context_text=next_context,
            )
            if chunks is not None and emitted >= chunks:
                break
        else:
            segment = segmenter.flush()
            if segment is not None and (chunks is None or emitted < chunks):
                emitted += 1
                _prepare_online_final_segment(
                    index=emitted,
                    samples=segment,
                    continues_previous=previous_closed_by_max_length,
                    segment_started_at=current_segment_started_at,
                    processor=processor,
                    early_preparer=early_preparer,
                    playback_queue=playback_queue,
                    bridge=bridge,
                    direction=direction,
                    target=target,
                    label=label,
                    show_latency=show_latency,
                    playback_gate=playback_gate,
                    async_runner=async_runner,
                    burst_tracker=burst_tracker,
                    transcript_deduplicator=transcript_deduplicator,
                    ledger=ledger,
                )
    finally:
        playback_queue.put(None)
        playback_worker.join()
        _close_live_bridge(bridge)
        async_runner.close()


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
    online_asr: bool,
    online_asr_early_prepare: bool,
    errors: queue.Queue[BaseException],
    playback_gate: _PlaybackGate,
    suppress_downlink_on_playback: bool,
    ledger: TranscriptLedger | None,
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
            online_asr=online_asr,
            online_asr_early_prepare=online_asr_early_prepare,
            playback_gate=playback_gate,
            suppress_downlink_on_playback=suppress_downlink_on_playback,
            ledger=ledger,
        )
    except BaseException as error:
        errors.put(error)


def _listen_worker(
    segment_queue: queue.Queue[tuple[int, SpeechSegment] | None],
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    label: str,
    show_latency: bool,
    playback_gate: _PlaybackGate | None,
    burst_tracker: _BurstTracker | None = None,
    transcript_deduplicator: _TranscriptOverlapDeduplicator | None = None,
    async_runner: _AsyncLoopRunner | None = None,
    ledger: TranscriptLedger | None = None,
) -> None:
    """后台处理 ASR / 翻译 / 合成，避免阻塞继续采集。"""
    runner = async_runner or _AsyncLoopRunner()
    owns_runner = async_runner is None
    tracker = burst_tracker or _BurstTracker()
    deduplicator = transcript_deduplicator or _TranscriptOverlapDeduplicator()
    try:
        while True:
            item = segment_queue.get()
            try:
                if item is None:
                    return
                index, segment = item
                _prepare_listen_segment(
                    index=index,
                    samples=segment.samples,
                    continues_previous=segment.continues_previous,
                    playback_queue=playback_queue,
                    bridge=bridge,
                    direction=direction,
                    target=target,
                    label=label,
                    show_latency=show_latency,
                    playback_gate=playback_gate,
                    burst_tracker=tracker,
                    transcript_deduplicator=deduplicator,
                    async_runner=runner,
                    ledger=ledger,
                )
            finally:
                segment_queue.task_done()
    finally:
        if owns_runner:
            runner.close()


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
    ledger: TranscriptLedger | None = None
    ledger_record_id: str = ""
    final_transcribed_at: float | None = None
    burst_id: int = 0


@dataclass
class _EarlyStablePrepareTask:
    """已提交到后台 prepare 的稳定增量任务。"""

    chunk: StableTranscriptChunk
    future: concurrent.futures.Future[PreparedSayResult]
    source_text: str
    submitted_at: float
    completed_at: float | None = None


class _EarlyStableChunkPreparer:
    """ASR callback 期间提前准备稳定增量，final 确认后再交给播放队列。"""

    def __init__(
        self,
        *,
        bridge: LivePushToTalkBridge,
        direction: AudioDirection,
        target: str,
        async_runner: _AsyncLoopRunner,
        enable_partial_prepare: bool = True,
        context_text: str = "",
    ) -> None:
        self._bridge = bridge
        self._direction = direction
        self._target = target
        self._async_runner = async_runner
        self._enable_partial_prepare = enable_partial_prepare
        self._context_text = context_text.strip()
        self._tasks: list[_EarlyStablePrepareTask] = []
        self._pending_source = ""
        self._submitted_prefix = ""
        self.final_chunk: StableTranscriptChunk | None = None

    def accept(self, chunk: StableTranscriptChunk) -> None:
        if chunk.kind is TranscriptKind.FINAL:
            self.final_chunk = chunk
            return
        if not self._enable_partial_prepare:
            return
        if not chunk.delta_text.strip():
            return
        self._pending_source = _join_transcript_parts(self._pending_source, chunk.delta_text)
        if not _is_translation_unit_ready(self._pending_source):
            return
        source_text = _playback_source_after_context_overlap(
            self._pending_source,
            context_text=self._context_for_next_submit(),
        )
        if not source_text.strip():
            self._pending_source = ""
            return
        self._submit(
            chunk=chunk,
            source_text=source_text,
            context_text=self._context_for_next_submit(),
        )
        self._submitted_prefix = _join_transcript_parts(self._submitted_prefix, source_text)
        self._pending_source = ""

    def _submit(self, *, chunk: StableTranscriptChunk, source_text: str, context_text: str) -> None:
        submitted_at = time.perf_counter()
        future = self._async_runner.submit(
            self._bridge.say_bridge.prepare(
                source_text,
                direction=self._direction,
                target=self._target,
                streaming=True,
                context_text=context_text,
            )
        )
        task = _EarlyStablePrepareTask(
            chunk=chunk,
            future=future,
            source_text=source_text,
            submitted_at=submitted_at,
        )
        future.add_done_callback(_done_callback_for(task))
        self._tasks.append(task)

    def _context_for_next_submit(self) -> str:
        return _join_transcript_parts(self._context_text, self._submitted_prefix)

    def confirmed_prepared(
        self,
        *,
        final_text: str,
        playback_text: str,
        final_transcribed_at: float,
        async_runner: _AsyncLoopRunner,
    ) -> list[tuple[PreparedSayResult, float, float]] | None:
        if not self._tasks or self.final_chunk is None:
            return None
        if self.final_chunk.text != final_text:
            return None
        confirmed_tasks = self._confirmed_tasks_for_playback(
            final_text=final_text,
            playback_text=playback_text,
        )
        if not confirmed_tasks:
            self.cancel_pending()
            return None
        prepared_items: list[tuple[PreparedSayResult, float, float]] = []
        try:
            confirmed_playback_text = _join_prepared_task_sources(confirmed_tasks)
            if not playback_text.startswith(confirmed_playback_text):
                self.cancel_pending()
                return None
            for task in confirmed_tasks:
                prepared_items.append(
                    (
                        task.future.result(),
                        task.completed_at or time.perf_counter(),
                        task.submitted_at,
                    )
                )
            final_delta_text = playback_text[len(confirmed_playback_text) :].strip()
            if final_delta_text.strip():
                prepared_items.append(
                    (
                        async_runner.run(
                            self._bridge.say_bridge.prepare(
                                final_delta_text,
                                direction=self._direction,
                                target=self._target,
                                streaming=True,
                                context_text=_join_transcript_parts(
                                    self._context_text,
                                    confirmed_playback_text,
                                ),
                            )
                        ),
                        time.perf_counter(),
                        final_transcribed_at,
                    )
                )
        except Exception:
            self.cancel_pending()
            return None
        _cancel_unconfirmed_prepare_tasks(self._tasks, confirmed_tasks=confirmed_tasks)
        return prepared_items

    def _confirmed_tasks_for_playback(
        self,
        *,
        final_text: str,
        playback_text: str,
    ) -> list[_EarlyStablePrepareTask]:
        confirmed_tasks = _confirmed_prefix_prepare_tasks(
            self._tasks,
            final_text=final_text,
        )
        if not confirmed_tasks:
            return []
        if playback_text == final_text:
            return confirmed_tasks
        return _confirmed_suffix_prepare_tasks(
            confirmed_tasks,
            playback_text=playback_text,
        )

    def cancel_pending(self) -> None:
        for task in self._tasks:
            task.future.cancel()


def _new_online_asr_processor(
    *,
    direction: AudioDirection,
    bridge: LivePushToTalkBridge,
) -> OnlineASRProcessor:
    return OnlineASRProcessor(
        direction=direction,
        transcribe_buffer=bridge.transcriber.transcribe,
    )


def _new_early_preparer(
    *,
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    async_runner: _AsyncLoopRunner,
    enable_partial_prepare: bool = True,
    context_text: str = "",
) -> _EarlyStableChunkPreparer:
    return _EarlyStableChunkPreparer(
        bridge=bridge,
        direction=direction,
        target=target,
        async_runner=async_runner,
        enable_partial_prepare=enable_partial_prepare,
        context_text=context_text,
    )


def _accept_online_partials(
    processor: OnlineASRProcessor,
    *,
    frame: np.ndarray,
    early_preparer: _EarlyStableChunkPreparer,
) -> None:
    try:
        chunks = processor.insert_audio_chunk(frame)
    except UserFacingError:
        return
    for chunk in chunks:
        early_preparer.accept(chunk)


def _prepare_online_final_segment(
    *,
    index: int,
    samples: np.ndarray,
    continues_previous: bool,
    segment_started_at: float | None = None,
    processor: OnlineASRProcessor,
    early_preparer: _EarlyStableChunkPreparer,
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    label: str,
    show_latency: bool,
    playback_gate: _PlaybackGate | None,
    async_runner: _AsyncLoopRunner,
    burst_tracker: _BurstTracker,
    transcript_deduplicator: _TranscriptOverlapDeduplicator,
    ledger: TranscriptLedger | None = None,
) -> None:
    started = segment_started_at or time.perf_counter()
    display_index = _display_index(label, index)
    if (
        direction is AudioDirection.DOWNLINK
        and playback_gate is not None
        and playback_gate.is_suppressed()
    ):
        typer.echo(f"{display_index} 已跳过：上行译音仍在写入 BlackHole，避免回灌。")
        early_preparer.cancel_pending()
        processor.reset()
        return
    try:
        text = bridge.transcriber.transcribe(samples)
    except UserFacingError as error:
        typer.echo(f"{display_index} {error.what_happened}")
        early_preparer.cancel_pending()
        processor.reset()
        return
    transcribed_at = time.perf_counter()
    typer.echo(f"{display_index} 在线识别：{text}")
    for chunk in processor.close_segment(final_text=text):
        early_preparer.accept(chunk)
    burst_id = burst_tracker.assign(continues_previous=continues_previous)
    context_text = transcript_deduplicator.context_for(burst_id=burst_id)
    deduped_text = _deduplicated_transcript_for_playback(
        text,
        display_index=display_index,
        burst_id=burst_id,
        deduplicator=transcript_deduplicator,
    )
    if deduped_text is None:
        early_preparer.cancel_pending()
        return
    prepared_items = _confirmed_early_prepared_items(
        early_preparer,
        final_text=text,
        deduped_text=deduped_text,
        final_transcribed_at=transcribed_at,
        async_runner=async_runner,
    )
    if prepared_items:
        for prepared, prepared_at, item_transcribed_at in prepared_items:
            typer.echo(f"{display_index} 稳定译文：{prepared.target_text}")
            _enqueue_prepared_playback(
                playback_queue,
                index=index,
                label=label,
                prepared=prepared,
                started=started,
                transcribed_at=item_transcribed_at,
                prepared_at=prepared_at,
                show_latency=show_latency,
                burst_id=burst_id,
                final_transcribed_at=transcribed_at,
                direction=direction,
                ledger=ledger,
            )
        return
    early_preparer.cancel_pending()
    try:
        prepared = async_runner.run(
            bridge.say_bridge.prepare(
                deduped_text,
                direction=direction,
                target=target,
                streaming=True,
                context_text=_context_text_for_playback(
                    existing_context=context_text,
                    final_text=text,
                    playback_text=deduped_text,
                ),
            )
        )
    except UserFacingError as error:
        typer.echo(f"{display_index} {error}")
        return
    prepared_at = time.perf_counter()
    typer.echo(f"{display_index} 首片译文：{prepared.target_text}")
    _enqueue_prepared_playback(
        playback_queue,
        index=index,
        label=label,
        prepared=prepared,
        started=started,
        transcribed_at=transcribed_at,
        prepared_at=prepared_at,
        show_latency=show_latency,
        burst_id=burst_id,
        final_transcribed_at=transcribed_at,
        direction=direction,
        ledger=ledger,
    )


def _mark_prepare_done(task: _EarlyStablePrepareTask) -> None:
    task.completed_at = time.perf_counter()


def _confirmed_prefix_prepare_tasks(
    tasks: list[_EarlyStablePrepareTask],
    *,
    final_text: str,
) -> list[_EarlyStablePrepareTask]:
    confirmed: list[_EarlyStablePrepareTask] = []
    for task in tasks:
        if not final_text.startswith(task.chunk.text):
            break
        confirmed.append(task)
    return confirmed


def _confirmed_suffix_prepare_tasks(
    tasks: list[_EarlyStablePrepareTask],
    *,
    playback_text: str,
) -> list[_EarlyStablePrepareTask]:
    playable = playback_text.strip()
    if not playable:
        return []
    best_start = len(tasks)
    best_text = ""
    for index in range(len(tasks) - 1, -1, -1):
        candidate_text = _join_prepared_task_sources(tasks[index:])
        if not playable.startswith(candidate_text):
            continue
        if len(candidate_text) > len(best_text):
            best_start = index
            best_text = candidate_text
    if not best_text:
        return []
    return tasks[best_start:]


def _join_prepared_task_sources(tasks: list[_EarlyStablePrepareTask]) -> str:
    text = ""
    for task in tasks:
        text = _join_transcript_parts(text, task.source_text)
    return text


def _cancel_unconfirmed_prepare_tasks(
    tasks: list[_EarlyStablePrepareTask],
    *,
    confirmed_tasks: list[_EarlyStablePrepareTask],
) -> None:
    confirmed_ids = {id(task) for task in confirmed_tasks}
    for task in tasks:
        if id(task) not in confirmed_ids:
            task.future.cancel()


def _done_callback_for(
    task: _EarlyStablePrepareTask,
) -> Callable[[concurrent.futures.Future[PreparedSayResult]], None]:
    def _callback(_future: concurrent.futures.Future[PreparedSayResult]) -> None:
        _mark_prepare_done(task)

    return _callback


def _join_transcript_parts(left: str, right: str) -> str:
    left_text = left.strip()
    right_text = right.strip()
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    if _contains_cjk(left_text) or _contains_cjk(right_text):
        return f"{left_text}{right_text}"
    return f"{left_text} {right_text}"


def _playback_source_after_context_overlap(source_text: str, *, context_text: str) -> str:
    source = source_text.strip()
    context = context_text.strip()
    if not source or not context:
        return source
    overlap_length = _safe_text_overlap_length(context, source)
    if overlap_length >= len(source):
        return ""
    return source[overlap_length:].strip()


def _is_translation_unit_ready(text: str) -> bool:
    source_text = text.strip()
    if not source_text:
        return False
    if source_text[-1] in TRANSLATION_UNIT_BOUNDARY_CHARS:
        return True
    if _contains_cjk(source_text):
        return len(source_text) >= REALTIME_EARLY_STABLE_MIN_CJK_CHARS
    return len(source_text.split()) >= REALTIME_EARLY_STABLE_MIN_WORDS


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


class _BurstTracker:
    """按录音/VAD 切段原因识别同一 utterance 内的连续段。"""

    def __init__(self) -> None:
        self._current_burst_id: int = 0

    def assign(self, *, continues_previous: bool) -> int:
        """返回该段的 burst_id；只有 VAD 层标记不连续时才递增。"""
        if self._current_burst_id == 0 or not continues_previous:
            self._current_burst_id += 1
        return self._current_burst_id

    @property
    def current_burst_id(self) -> int:
        return self._current_burst_id


class _TranscriptOverlapDeduplicator:
    """去掉相邻强制切段因音频 overlap 造成的重复前缀。"""

    def __init__(self) -> None:
        self._committed_text = ""
        self._last_burst_id: int | None = None

    def accept(self, text: str, *, burst_id: int) -> str:
        current = " ".join(text.split())
        if not current:
            return ""
        if self._last_burst_id != burst_id:
            self._last_burst_id = burst_id
            self._committed_text = current
            return current
        overlap_length = _safe_text_overlap_length(self._committed_text, current)
        if overlap_length >= len(current):
            return current
        emitted = current[overlap_length:].strip()
        if emitted:
            self._committed_text = _join_transcript_parts(self._committed_text, emitted)
        return emitted

    @property
    def committed_text(self) -> str:
        return self._committed_text

    def context_for(self, *, burst_id: int) -> str:
        if self._last_burst_id != burst_id:
            return ""
        return self._committed_text


def _safe_text_overlap_length(previous: str, current: str) -> int:
    max_len = min(len(previous), len(current))
    for length in range(max_len, 0, -1):
        if not previous.endswith(current[:length]):
            continue
        overlap = current[:length]
        if _contains_cjk(overlap):
            if len(overlap) >= MIN_CJK_OVERLAP_CHARS:
                return length
            continue
        if len(overlap.split()) >= MIN_WORD_OVERLAP and _has_complete_word_overlap(
            previous,
            current,
            length=length,
        ):
            return length
    return 0


def _has_complete_word_overlap(previous: str, current: str, *, length: int) -> bool:
    previous_start = len(previous) - length
    return _has_word_boundary_before(previous, previous_start) and _has_word_boundary_after(
        current,
        length,
    )


def _has_word_boundary_before(text: str, index: int) -> bool:
    return index <= 0 or not _is_word_char(text[index - 1])


def _has_word_boundary_after(text: str, index: int) -> bool:
    return index >= len(text) or not _is_word_char(text[index])


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _prepare_listen_segment(
    *,
    index: int,
    samples: np.ndarray,
    continues_previous: bool = False,
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    direction: AudioDirection,
    target: str,
    label: str,
    show_latency: bool,
    playback_gate: _PlaybackGate | None,
    async_runner: _AsyncLoopRunner | None = None,
    burst_tracker: _BurstTracker | None = None,
    transcript_deduplicator: _TranscriptOverlapDeduplicator | None = None,
    ledger: TranscriptLedger | None = None,
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
    runner = async_runner or _AsyncLoopRunner()
    owns_runner = async_runner is None
    early_preparer = (
        _EarlyStableChunkPreparer(
            bridge=bridge,
            direction=direction,
            target=target,
            async_runner=runner,
        )
        if REALTIME_EARLY_STABLE_CHUNK_PREPARE
        else None
    )
    try:
        try:
            text = _transcribe_with_stable_callback(
                bridge.transcriber.transcribe,
                samples,
                stable_chunk_callback=early_preparer.accept if early_preparer is not None else None,
            )
        except UserFacingError as error:
            typer.echo(f"{display_index} {error.what_happened}")
            _cancel_early_preparer(early_preparer)
            return
        transcribed_at = time.perf_counter()
        typer.echo(f"{display_index} 识别：{text}")
        tracker = burst_tracker or _BurstTracker()
        burst_id = tracker.assign(continues_previous=continues_previous)
        deduplicator = transcript_deduplicator or _TranscriptOverlapDeduplicator()
        context_text = deduplicator.context_for(burst_id=burst_id)
        deduped_text = _deduplicated_transcript_for_playback(
            text,
            display_index=display_index,
            burst_id=burst_id,
            deduplicator=deduplicator,
        )
        if deduped_text is None:
            _cancel_early_preparer(early_preparer)
            return
        prepared_items = _confirmed_early_prepared_items(
            early_preparer,
            final_text=text,
            deduped_text=deduped_text,
            final_transcribed_at=transcribed_at,
            async_runner=runner,
        )
        if prepared_items:
            for prepared, prepared_at, item_transcribed_at in prepared_items:
                typer.echo(f"{display_index} 稳定译文：{prepared.target_text}")
                _enqueue_prepared_playback(
                    playback_queue,
                    index=index,
                    label=label,
                    prepared=prepared,
                    started=started,
                    transcribed_at=item_transcribed_at,
                    prepared_at=prepared_at,
                    show_latency=show_latency,
                    burst_id=burst_id,
                    final_transcribed_at=transcribed_at,
                    direction=direction,
                    ledger=ledger,
                )
            return
        if early_preparer is not None:
            early_preparer.cancel_pending()
        try:
            prepared = runner.run(
                bridge.say_bridge.prepare(
                    deduped_text,
                    direction=direction,
                    target=target,
                    streaming=True,
                    context_text=_context_text_for_playback(
                        existing_context=context_text,
                        final_text=text,
                        playback_text=deduped_text,
                    ),
                )
            )
        except UserFacingError as error:
            typer.echo(f"{display_index} {error}")
            return
        prepared_at = time.perf_counter()
        typer.echo(f"{display_index} 首片译文：{prepared.target_text}")
        _enqueue_prepared_playback(
            playback_queue,
            index=index,
            label=label,
            prepared=prepared,
            started=started,
            transcribed_at=transcribed_at,
            prepared_at=prepared_at,
            show_latency=show_latency,
            burst_id=burst_id,
            final_transcribed_at=transcribed_at,
            direction=direction,
            ledger=ledger,
        )
    finally:
        if owns_runner:
            runner.close()


def _cancel_early_preparer(early_preparer: _EarlyStableChunkPreparer | None) -> None:
    if early_preparer is not None:
        early_preparer.cancel_pending()


def _transcribe_with_stable_callback(
    transcribe: Callable[..., str],
    samples: np.ndarray,
    *,
    stable_chunk_callback: Callable[[StableTranscriptChunk], None] | None,
) -> str:
    if stable_chunk_callback is None or not _accepts_stable_chunk_callback(transcribe):
        return transcribe(samples)
    return transcribe(samples, stable_chunk_callback=stable_chunk_callback)


def _deduplicated_transcript_for_playback(
    text: str,
    *,
    display_index: str,
    burst_id: int,
    deduplicator: _TranscriptOverlapDeduplicator,
) -> str | None:
    deduped_text = deduplicator.accept(text, burst_id=burst_id)
    if not deduped_text:
        typer.echo(f"{display_index} 已跳过：该段只包含上一段 overlap 重复文本。")
        return None
    if deduped_text != text:
        typer.echo(f"{display_index} 去重后：{deduped_text}")
    return deduped_text


def _confirmed_early_prepared_items(
    early_preparer: _EarlyStableChunkPreparer | None,
    *,
    final_text: str,
    deduped_text: str,
    final_transcribed_at: float,
    async_runner: _AsyncLoopRunner,
) -> list[tuple[PreparedSayResult, float, float]] | None:
    if early_preparer is None:
        return None
    return early_preparer.confirmed_prepared(
        final_text=final_text,
        playback_text=deduped_text,
        final_transcribed_at=final_transcribed_at,
        async_runner=async_runner,
    )


def _context_text_for_playback(
    *,
    existing_context: str,
    final_text: str,
    playback_text: str,
) -> str:
    context = existing_context.strip()
    if context:
        return context
    final_source = final_text.strip()
    playable = playback_text.strip()
    if not playable or final_source == playable or not final_source.endswith(playable):
        return ""
    return final_source[: -len(playable)].strip()


def _accepts_stable_chunk_callback(transcribe: Callable[..., str]) -> bool:
    try:
        parameters = inspect.signature(transcribe).parameters
    except (TypeError, ValueError):
        return False
    return "stable_chunk_callback" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _enqueue_prepared_playback(
    playback_queue: queue.Queue[_PendingPlayback | None],
    *,
    index: int,
    label: str,
    prepared: PreparedSayResult,
    started: float,
    transcribed_at: float,
    prepared_at: float,
    show_latency: bool,
    burst_id: int,
    direction: AudioDirection,
    ledger: TranscriptLedger | None = None,
    final_transcribed_at: float | None = None,
) -> None:
    display_index = _display_index(label, index)
    drop_old_bursts = playback_queue.qsize() >= REALTIME_PLAYBACK_OLD_BURST_DROP_DEPTH
    dropped_pending = _drop_pending_playbacks(
        playback_queue,
        current_burst_id=burst_id,
        drop_old_bursts=drop_old_bursts,
    )
    if dropped_pending:
        typer.echo(
            f"{display_index} 已丢弃 {dropped_pending} 个跨 burst 旧段：保留同 burst 多段连续播放。"
        )
    queue_depth_at_enqueue = playback_queue.qsize()
    if queue_depth_at_enqueue >= REALTIME_PLAYBACK_BACKLOG_WARNING_DEPTH:
        typer.echo(
            f"{display_index} 实时播放积压 {queue_depth_at_enqueue} 段：内容会保留，"
            "但端到端延迟正在升高。"
        )
    ledger_record_id = ""
    if ledger is not None:
        ledger_record_id = ledger.record_prepared(
            label=label,
            index=index,
            burst_id=burst_id,
            direction=direction,
            source_text=prepared.source_text,
            target_text=_ledger_target_text(prepared),
            preview_only=prepared.pcm_iterator is not None,
        )
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
            ledger=ledger,
            ledger_record_id=ledger_record_id,
            final_transcribed_at=final_transcribed_at,
            burst_id=burst_id,
        )
    )


def _drop_pending_playbacks(
    playback_queue: queue.Queue[_PendingPlayback | None],
    *,
    current_burst_id: int = 0,
    drop_old_bursts: bool = False,
) -> int:
    """可选丢弃旧 utterance pending；默认保留，避免长句切片误分 burst 后丢失。"""
    if not drop_old_bursts:
        return 0
    dropped = 0
    kept: list[_PendingPlayback] = []
    sentinel_seen = False
    while True:
        try:
            item = playback_queue.get_nowait()
        except queue.Empty:
            break
        if item is None:
            sentinel_seen = True
            break
        playback_queue.task_done()
        if item.burst_id < current_burst_id:
            if item.ledger is not None and item.ledger_record_id:
                item.ledger.record_drop(
                    record_id=item.ledger_record_id,
                    reason="cross_burst_backlog",
                )
            dropped += 1
        else:
            kept.append(item)
    for item in kept:
        playback_queue.put(item)
    if sentinel_seen:
        playback_queue.put(None)
    return dropped


def _ledger_target_text(prepared: PreparedSayResult) -> str:
    """尽量取 streaming producer 已确认的完整译文，回退到首片译文。"""
    iterator = prepared.pcm_iterator
    if iterator is not None:
        snapshot = getattr(iterator, "target_text_snapshot", None)
        if callable(snapshot):
            text = str(snapshot()).strip()
            if text:
                return text
    return prepared.target_text


def _listen_playback_worker(
    playback_queue: queue.Queue[_PendingPlayback | None],
    bridge: LivePushToTalkBridge,
    playback_gate: _PlaybackGate | None = None,
    suppress_downlink_on_playback: bool = False,
) -> None:
    """按顺序播放已准备好的译音；同 burst 段豁免 stale 检查保护长句多段连续输出。"""
    last_played_burst_id: int | None = None
    while True:
        item = playback_queue.get()
        try:
            if item is None:
                return
            same_burst_as_last = (
                last_played_burst_id is not None and item.burst_id == last_played_burst_id
            )
            stale_wait_s = time.perf_counter() - item.prepared_at
            stale_window_s = REALTIME_STALE_PLAYBACK_WAIT_SECONDS
            if _should_skip_stale_playback(
                same_burst_as_last=same_burst_as_last,
                stale_wait_s=stale_wait_s,
                stale_window_s=stale_window_s,
            ):
                if item.ledger is not None and item.ledger_record_id:
                    item.ledger.record_playback(
                        record_id=item.ledger_record_id,
                        status="skipped_stale",
                        target_text=_ledger_target_text(item.prepared),
                        reason="stale_playback_window",
                        wait_seconds=stale_wait_s,
                    )
                typer.echo(
                    f"{_display_index(item.label, item.index)} 已丢弃：译音等待播放 "
                    f"{stale_wait_s:.2f}s，超过实时窗口 "
                    f"{stale_window_s:.2f}s。"
                )
                continue
            suppression_started = False
            if suppress_downlink_on_playback and playback_gate is not None:
                playback_gate.begin_playback_suppression(
                    hard_cap_seconds=_playback_gate_suppression_seconds(
                        item.prepared,
                        max_playback_seconds=REALTIME_MAX_PLAYBACK_SECONDS,
                    )
                    + 0.8,
                )
                suppression_started = True
            playback_started = time.perf_counter()
            playback_succeeded = False
            try:
                result = asyncio.run(
                    _play_prepared_for_listen(
                        bridge,
                        item.prepared,
                        max_playback_seconds=REALTIME_MAX_PLAYBACK_SECONDS,
                    )
                )
                playback_succeeded = True
            except UserFacingError as error:
                if item.ledger is not None and item.ledger_record_id:
                    item.ledger.record_playback(
                        record_id=item.ledger_record_id,
                        status="failed",
                        target_text=_ledger_target_text(item.prepared),
                        reason=error.code,
                    )
                typer.echo(f"{_display_index(item.label, item.index)} {error}")
                continue
            finally:
                if playback_gate is not None and suppression_started:
                    tail_seconds = 0.8 if playback_succeeded else 0.0
                    playback_gate.finish_playback_suppression(tail_seconds=tail_seconds)
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
            if item.ledger is not None and item.ledger_record_id:
                status = "truncated" if result.playback_truncated else "played"
                item.ledger.record_playback(
                    record_id=item.ledger_record_id,
                    status=status,
                    target_text=_ledger_target_text(item.prepared),
                )
            last_played_burst_id = item.burst_id
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
    final_transcribed_at = item.final_transcribed_at or item.transcribed_at
    asr_s = _elapsed_seconds(final_transcribed_at, item.started)
    source_ready_s = _elapsed_seconds(item.transcribed_at, item.started)
    prepare_wall_s = _elapsed_seconds(item.prepared_at, item.transcribed_at)
    queue_wait_s = _elapsed_seconds(playback_started, item.prepared_at)
    first_pcm_value = _elapsed_seconds_value(
        playback_started + result.first_pcm_latency_s,
        item.transcribed_at,
    )
    first_pcm_s = _format_optional_seconds(first_pcm_value)
    first_write_s = _first_write_latency_from_transcription(
        item,
        result=result,
        playback_started=playback_started,
    )
    first_byte_s = first_write_s if first_write_s is not None else first_pcm_value
    total_s = _elapsed_seconds(completed_at, item.started)
    timing_note = _timing_inversion_note(
        item,
        playback_started=playback_started,
        completed_at=completed_at,
    )
    typer.echo(
        f"{_display_index(item.label, item.index)} "
        f"耗时：ASR {asr_s} / "
        f"源文可用 {source_ready_s} / "
        f"MT首T {result.mt_first_token_latency_s:.2f}s / "
        f"MT总 {result.translation_latency_s:.2f}s / prepare墙钟 {prepare_wall_s} / "
        f"TTS {result.tts_latency_s:.2f}s / 解码 {result.decode_latency_s:.2f}s / "
        f"排队 {queue_wait_s}"
        f"(q={item.queue_depth_at_enqueue},drop={item.dropped_pending_before_enqueue}) / "
        f"首PCM {first_pcm_s} / 首写 {_format_optional_seconds(first_write_s)} / "
        f"首字节 {_format_optional_seconds(first_byte_s)} / "
        f"播放 {result.playback_latency_s:.2f}s{_format_truncated(result)} / "
        f"总计 {total_s}{timing_note}"
    )


def _first_write_latency_from_transcription(
    item: _PendingPlayback,
    *,
    result: SayResult,
    playback_started: float,
) -> float | None:
    if result.first_playback_write_latency_s is None:
        return None
    return _elapsed_seconds_value(
        playback_started + result.first_playback_write_latency_s,
        item.transcribed_at,
    )


def _elapsed_seconds(later: float, earlier: float) -> str:
    value = _elapsed_seconds_value(later, earlier)
    return "n/a" if value is None else f"{value:.2f}s"


def _elapsed_seconds_value(later: float, earlier: float) -> float | None:
    value = later - earlier
    if value < 0:
        return None
    return value


def _timing_inversion_note(
    item: _PendingPlayback,
    *,
    playback_started: float,
    completed_at: float,
) -> str:
    events = [
        ("源文可用", item.transcribed_at),
        ("prepare完成", item.prepared_at),
        ("播放开始", playback_started),
        ("播放完成", completed_at),
    ]
    final_transcribed_at = item.final_transcribed_at or item.transcribed_at
    events.insert(1, ("final识别", final_transcribed_at))
    inverted = [name for name, timestamp in events if timestamp < item.started]
    if not inverted:
        return ""
    return f" / 计时异常：{','.join(inverted)} 早于段起点"


def _format_optional_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}s"


def _format_truncated(result: SayResult) -> str:
    if not result.playback_truncated:
        return ""
    if REALTIME_MAX_PLAYBACK_SECONDS is None:
        return "(截断)"
    return f"(截断≤{REALTIME_MAX_PLAYBACK_SECONDS:.1f}s)"


def _display_index(label: str, index: int) -> str:
    if not label:
        return f"[{index}]"
    return f"[{label} {index}]"


def _safe_default_device(lookup: Callable[[], AudioDevice]) -> AudioDevice | None:
    try:
        return lookup()
    except UserFacingError:
        return None


def _microphone_input_device(*, input_device_name: str) -> AudioDevice:
    """解析 tvi 实际监听的真实麦克风，供启动日志与录音器保持一致。"""
    probe = AudioDeviceProbe()
    if input_device_name:
        return probe.find_input_device_by_name(input_device_name)
    return probe.get_default_input()


def _default_output_device() -> AudioDevice:
    """解析 tvi 下行译音实际播放到的 macOS 默认输出。"""
    return AudioDeviceProbe().get_default_output()


def _print_audio_devices(
    devices: list[AudioDevice],
    *,
    default_device: AudioDevice | None,
    uplink_virtual_device_name: str,
    downlink_virtual_device_name: str,
) -> None:
    if not devices:
        typer.echo("  - 未检测到")
        return
    for device in devices:
        markers = _audio_device_markers(
            device,
            default_device=default_device,
            uplink_virtual_device_name=uplink_virtual_device_name,
            downlink_virtual_device_name=downlink_virtual_device_name,
        )
        marker_text = "" if not markers else f" [{', '.join(markers)}]"
        typer.echo(
            f"  - #{device.index} {device.name} "
            f"(in={device.max_input_channels}, out={device.max_output_channels}){marker_text}"
        )


def _audio_device_markers(
    device: AudioDevice,
    *,
    default_device: AudioDevice | None,
    uplink_virtual_device_name: str,
    downlink_virtual_device_name: str,
) -> list[str]:
    markers: list[str] = []
    if default_device is not None and device.index == default_device.index:
        markers.append("default")
    if _looks_like_route_device_name(
        device.name,
        uplink_virtual_device_name=uplink_virtual_device_name,
        downlink_virtual_device_name=downlink_virtual_device_name,
    ):
        markers.append("virtual-route")
    return markers


def _print_physical_candidates(
    title: str,
    devices: list[AudioDevice],
    *,
    uplink_virtual_device_name: str,
    downlink_virtual_device_name: str,
) -> None:
    candidates = [
        device
        for device in devices
        if not _looks_like_route_device_name(
            device.name,
            uplink_virtual_device_name=uplink_virtual_device_name,
            downlink_virtual_device_name=downlink_virtual_device_name,
        )
    ]
    if not candidates:
        typer.echo(f"{title}：未检测到")
        return
    names = ", ".join(f"{device.name} (index={device.index})" for device in candidates)
    typer.echo(f"{title}：{names}")


def _looks_like_route_device_name(
    device_name: str,
    *,
    uplink_virtual_device_name: str,
    downlink_virtual_device_name: str,
) -> bool:
    lower_name = device_name.lower()
    return (
        device_name in {uplink_virtual_device_name, downlink_virtual_device_name}
        or "blackhole" in lower_name
        or "aggregate" in lower_name
        or "聚合" in device_name
        or "同传" in device_name
    )


def _prepared_audio_seconds(prepared: PreparedSayResult) -> float:
    if prepared.pcm.size > 0:
        return float(prepared.pcm.size) / 16000
    if prepared.pcm_iterator is not None:
        return 10.0
    return min(10.0, max(1.0, len(prepared.target_text) / 8.0))


def _should_skip_stale_playback(
    *,
    same_burst_as_last: bool,
    stale_wait_s: float,
    stale_window_s: float | None,
) -> bool:
    if same_burst_as_last or stale_window_s is None:
        return False
    return stale_wait_s > stale_window_s


def _playback_gate_suppression_seconds(
    prepared: PreparedSayResult,
    *,
    max_playback_seconds: float | None,
) -> float:
    audio_seconds = _prepared_audio_seconds(prepared)
    if max_playback_seconds is None:
        return audio_seconds
    return min(audio_seconds, max_playback_seconds)


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
        mark = _readiness_mark(check.status)
        typer.echo(f"[{mark}] {check.title}: {check.detail}")
        if check.status is CheckStatus.FAIL and check.next_action:
            typer.echo(f"      {check.next_action}")


def _readiness_mark(status: CheckStatus) -> str:
    if status is CheckStatus.PASS:
        return "OK"
    if status is CheckStatus.INFO:
        return "INFO"
    return "FAIL"


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
