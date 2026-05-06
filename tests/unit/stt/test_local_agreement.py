"""LocalAgreement partial 提交策略测试。"""

from __future__ import annotations

from teams_voice_interpreter.stt.whisper_streaming import LocalAgreementCommitter


def test_local_agreement_commits_stable_chinese_prefix() -> None:
    """中文 partial 连续稳定后应当按字符前缀提交，降低首段等待时间。"""
    committer = LocalAgreementCommitter()

    assert committer.accept_partial("我们今天") == ""
    assert committer.accept_partial("我们今天讨论现金流") == "我们今天"
    assert committer.accept_partial("我们今天讨论现金流预测") == "讨论现金流"


def test_local_agreement_avoids_incomplete_english_words() -> None:
    """英文 partial 只能提交完整词，不能把半个单词送入翻译。"""
    committer = LocalAgreementCommitter()

    assert committer.accept_partial("we discuss ca") == ""
    assert committer.accept_partial("we discuss cash") == "we discuss"
    assert committer.accept_partial("we discuss cash flow") == ""


def test_local_agreement_flushes_uncommitted_final_text() -> None:
    """final 到来时必须补齐所有未提交尾巴，保证长句不丢。"""
    committer = LocalAgreementCommitter()

    assert committer.accept_partial("we discuss ca") == ""
    assert committer.accept_partial("we discuss cash") == "we discuss"

    final = committer.accept_final("we discuss cash flow forecast")

    assert final.text == "we discuss cash flow forecast"
    assert final.delta_text == "cash flow forecast"
    assert not final.revision


def test_local_agreement_marks_final_revision_without_duplicate_delta() -> None:
    """final 改写已提交前缀时，不得把完整 final 当增量重复送入下游。"""
    committer = LocalAgreementCommitter()

    assert committer.accept_partial("我们今天") == ""
    assert committer.accept_partial("我们今天讨论") == "我们今天"

    final = committer.accept_final("今天我们讨论")

    assert final.text == "今天我们讨论"
    assert final.delta_text == ""
    assert final.revision


def test_local_agreement_resets_between_segments() -> None:
    """新 segment 必须清空已提交前缀，避免上一句污染下一句。"""
    committer = LocalAgreementCommitter()
    committer.accept_partial("我们今天")
    committer.accept_partial("我们今天讨论")
    committer.reset()

    assert committer.accept_partial("下一项") == ""
    assert committer.accept_partial("下一项预算") == "下一项"
