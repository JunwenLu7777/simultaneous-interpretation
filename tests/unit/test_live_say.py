"""短句真实发声桥测试。"""

from teams_voice_interpreter.live_say import _preview_text, _target_text_from_chunks
from teams_voice_interpreter.mt.deepseek_client import TranslationChunk


def test_target_text_from_chunks_joins_deepseek_delta_text() -> None:
    """DeepSeek delta 分片必须拼成完整译文，不能只取最后一个标点。"""
    chunks = [
        TranslationChunk(kind="delta", text="Hello"),
        TranslationChunk(kind="delta", text=", let's begin"),
        TranslationChunk(kind="delta", text=" the meeting"),
        TranslationChunk(kind="delta", text="."),
    ]

    assert _target_text_from_chunks(chunks) == "Hello, let's begin the meeting."


def test_preview_text_compacts_and_truncates_long_text() -> None:
    """错误提示中的译文预览应保持单行且长度受控。"""
    text = _preview_text("Hello\n\nworld " + "x" * 100, max_length=20)

    assert text == "Hello world xxxxxxxx..."
