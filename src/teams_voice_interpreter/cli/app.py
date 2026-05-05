"""Typer CLI 入口。"""

import asyncio
import json

import typer

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.manager import DEFAULT_MANAGER

app = typer.Typer(help="Teams 双向实时语音同传桥")


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


def main() -> None:
    """运行命令行入口。"""
    app()
