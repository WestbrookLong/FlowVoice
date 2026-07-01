from server import BridgeSettings, render_text
from text_agent.manager import TextAgentManager
from text_agent.providers.preview import PreviewTextPolishProvider


class CapturingProvider:
    name = "capture"

    def __init__(self):
        self.segment_calls = []
        self.global_calls = []

    def available(self):
        return True

    def polish_segment(self, previous_summaries, previous_raw_text, new_raw_text, style):
        self.segment_calls.append((list(previous_summaries), previous_raw_text, new_raw_text, style))
        return {
            "title": f"主题 {len(self.segment_calls)}",
            "summary": f"segment:{new_raw_text}",
            "keyPoints": [new_raw_text],
            "actionItems": [],
        }

    def polish_global(self, raw_text, segment_summaries, style):
        self.global_calls.append((raw_text, list(segment_summaries), style))
        return f"global:{raw_text}"


def test_text_agent_polishes_segments_with_context():
    provider = CapturingProvider()
    manager = TextAgentManager(provider=provider, trigger_chars=20, active_tail_chars=0)

    manager.observe_mobile_text("baseline")
    manager.start("meeting_notes")
    first_segment = "第一段内容需要足够长才能触发整理所以这里继续补充一些字"
    second_segment = "第二段内容同样需要足够长才能触发整理所以这里继续补充一些字"
    manager.update_text(f"baseline{first_segment}")
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)

    manager.update_text(f"baseline{first_segment}{second_segment}")
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)

    assert provider.segment_calls[0] == ([], "", first_segment, "meeting_notes")
    assert provider.segment_calls[1][0][0]["summary"] == f"segment:{first_segment}"
    assert provider.segment_calls[1][1] == first_segment
    assert provider.segment_calls[1][2] == second_segment


def test_text_agent_stop_global_polish_copies_without_inserting_and_clears_raw_temp():
    events = []
    provider = CapturingProvider()
    manager = TextAgentManager(
        provider=provider,
        copy_callback=lambda text: events.append(("copy", text)),
        insert_callback=lambda text: events.append(("insert", text)),
        trigger_chars=5,
    )

    manager.start("faithful_cleanup")
    manager.update_text("需要整理并注入的最终文本")
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)
    session = manager.stop(copy=True, insert=False)

    assert provider.global_calls
    assert provider.global_calls[0][0] == "需要整理并注入的最终文本"
    assert session.status == "done"
    assert session.raw_text == ""
    assert session.segment_summaries
    assert session.final_text == "global:需要整理并注入的最终文本"
    assert session.copied is True
    assert session.inserted is False
    assert events[0] == ("copy", "global:需要整理并注入的最终文本")
    assert len(events) == 1


def test_text_agent_pause_preserves_temp_but_ignores_mobile_text_until_resume():
    manager = TextAgentManager(provider=PreviewTextPolishProvider(), trigger_chars=20)

    manager.observe_mobile_text("")
    manager.start("meeting_notes")
    manager.update_text("记录中的文本")
    manager.pause()
    manager.update_text("记录中的文本暂停期间普通注入文本")

    session = manager.get_state()["activeSession"]
    assert session["status"] == "paused"
    assert session["rawText"] == "记录中的文本"

    manager.observe_mobile_text("记录中的文本暂停期间普通注入文本")
    manager.resume()
    manager.update_text("记录中的文本暂停期间普通注入文本恢复后的文本")
    session = manager.get_state()["activeSession"]
    assert session["rawText"] == "记录中的文本恢复后的文本"


def test_text_agent_keeps_fifteen_character_active_tail_until_stop():
    provider = CapturingProvider()
    manager = TextAgentManager(provider=provider, trigger_chars=20, active_tail_chars=15)
    source = "abcdefghijklmnopqrstuvwxyz123456789"

    manager.start("meeting_notes")
    manager.update_text(source)
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)

    assert provider.segment_calls[0][2] == source[:-15]
    assert manager.get_state()["activeSession"]["rawText"] == source

    manager.stop(copy=False, insert=False)
    assert provider.segment_calls[-1][2] == source[-15:]
    assert provider.global_calls[-1][0] == source


def test_text_agent_replaces_revised_mobile_tail_and_invalidates_affected_segments():
    provider = CapturingProvider()
    manager = TextAgentManager(provider=provider, trigger_chars=20, active_tail_chars=0)
    original = "A" * 20 + "B" * 20
    revised = "A" * 10 + "C" * 30

    manager.start("meeting_notes")
    manager.update_text(original)
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)

    manager.update_text(revised)
    if manager.polish_thread is not None:
        manager.polish_thread.join(timeout=2)

    session = manager.get_state()["activeSession"]
    assert session["rawText"] == revised
    assert session["segmentSummaries"][0]["summary"] == f"segment:{revised}"


def test_text_agent_uses_phone_punctuation_filter_for_processed_snapshot():
    manager = TextAgentManager(provider=PreviewTextPolishProvider(), trigger_chars=20)
    source = "你好，世界！这是测试。"
    settings = BridgeSettings(filter_punctuation=True)

    manager.start("meeting_notes")
    active_source = manager.capture_active_source(source)
    manager.update_text(
        source,
        render_text(active_source, settings),
        active_source_text=active_source,
    )

    assert manager.get_state()["activeSession"]["rawText"] == "你好世界这是测试"


def test_partial_notes_are_copied_as_structured_markdown():
    copied = []
    manager = TextAgentManager(
        provider=PreviewTextPolishProvider(),
        copy_callback=copied.append,
    )
    manager.start("meeting_notes")
    session = manager.sessions[manager.active_session_id]
    session.segment_summaries = [
        {
            "title": "产品方向",
            "summary": "确认先完成桌面端会议纪要能力。",
            "keyPoints": ["保留十五字活动尾部", "手机端逻辑保持不变"],
            "actionItems": [
                {
                    "text": "完成浮标透明化",
                    "owner": "Westbrook",
                    "deadline": "本周五",
                }
            ],
        },
        {
            "title": "下一步",
            "summary": "完成测试后进入体验优化。",
            "keyPoints": [],
            "actionItems": [],
        },
    ]

    markdown = manager.copy_partial_notes()

    assert markdown.startswith("# 实时会议纪要")
    assert "## 1. 产品方向" in markdown
    assert "- 保留十五字活动尾部" in markdown
    assert "- [ ] 完成浮标透明化（负责人：Westbrook；截止时间：本周五）" in markdown
    assert "## 2. 下一步" in markdown
    assert copied == [markdown]


def test_copy_partial_notes_rejects_empty_segments_without_touching_clipboard():
    copied = []
    manager = TextAgentManager(
        provider=PreviewTextPolishProvider(),
        copy_callback=copied.append,
    )
    manager.start("meeting_notes")

    try:
        manager.copy_partial_notes()
    except RuntimeError as exc:
        assert "No partial meeting notes" in str(exc)
    else:
        raise AssertionError("Expected empty partial notes to be rejected.")

    assert copied == []
