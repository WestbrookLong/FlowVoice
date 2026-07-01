from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import desktop_client
from asr.base import ASREvent


def make_thread() -> desktop_client.DesktopVoiceThread:
    config = {
        "engine": "funasr",
        "funasrMode": "streaming",
        "punctuationStrategy": "none",
        "voiceCommands": True,
    }
    settings = desktop_client.bridge_settings_from_desktop_config(config)
    return desktop_client.DesktopVoiceThread(ROOT / "models", settings, config)


def with_input_recorder():
    ops: list[tuple[str, object]] = []

    desktop_client.type_text = lambda text: ops.append(("type", text))
    desktop_client.send_backspace_chunks = lambda count: ops.append(("backspace", count))

    class FakeFlowInputSession:
        def sync_state(self, text, settings):
            ops.append(("sync", text))

        def reset(self):
            ops.append(("reset", None))

    desktop_client.FlowInputSession = FakeFlowInputSession
    return ops


def test_minimal_patch_append() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.composition_text = "流式输入"

    thread._replace_composition("流式输入问题")

    assert ops == [("type", "问题")]
    assert thread.composition_text == "流式输入问题"


def test_minimal_patch_tail_correction() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.composition_text = "流式输入问题"

    thread._replace_composition("流式输入现象")

    assert ops == [("backspace", 2), ("type", "现象")]
    assert thread.composition_text == "流式输入现象"


def test_partial_long_text_segments_to_tail_composition() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.composition_tail_chars = 6
    full = "我们现在讨论一下流式输入的问题"

    thread._handle_streaming_partial(ASREvent(type="partial", text=full, stable_text=""))

    assert thread.committed_partial_text == full[:-6]
    assert thread.composition_text == full[-6:]
    assert ops == [("type", full[:-6]), ("type", full[-6:])]


def test_streaming_route_uses_virtual_input_session_for_partial() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_ime_asr_events([ASREvent(type="partial", text="我们现在讨论")])

    assert ops == [("sync", "我们现在讨论")]
    assert thread.pending_partial_text == "我们现在讨论"
    assert thread.composition_text == ""


def test_streaming_route_allows_virtual_prefix_revision() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_ime_asr_events([ASREvent(type="partial", text="我门现在讨论")])
    thread._handle_ime_asr_events([ASREvent(type="partial", text="我们现在讨论")])

    assert ops == [("sync", "我门现在讨论"), ("sync", "我们现在讨论")]
    assert thread.pending_partial_text == "我们现在讨论"


def test_streaming_final_commits_virtual_text() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_ime_asr_events([ASREvent(type="partial", text="我们现在")])
    thread._handle_ime_asr_events([ASREvent(type="final", text="我们现在讨论")])
    thread._handle_ime_asr_events([ASREvent(type="partial", text="流式输入")])

    assert ops == [
        ("sync", "我们现在"),
        ("sync", "我们现在讨论"),
        ("sync", "我们现在讨论流式输入"),
    ]
    assert thread.committed_text == "我们现在讨论"
    assert thread.pending_partial_text == "流式输入"


def test_virtual_input_window_resets_after_50_chars_without_deleting() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    text = "a" * desktop_client.DESKTOP_VOICE_VIRTUAL_RESET_CHARS

    thread._handle_ime_asr_events([ASREvent(type="final", text=text)])
    thread._handle_ime_asr_events([ASREvent(type="partial", text="next")])

    assert ops == [
        ("sync", text),
        ("reset", None),
        ("sync", "next"),
    ]
    assert thread.committed_text == ""
    assert thread.pending_partial_text == "next"


def test_async_final_rescore_replaces_matching_suffix() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_ime_asr_events([ASREvent(type="final", text="我们讨论", source="streaming_final")])
    thread._handle_ime_asr_events(
        [
            ASREvent(
                type="final",
                text="我们正在讨论",
                stable_text="我们讨论",
                source="final_rescore",
            )
        ]
    )

    assert ops == [
        ("sync", "我们讨论"),
        ("sync", "我们正在讨论"),
    ]
    assert thread.committed_text == "我们正在讨论"
    assert thread.pending_partial_text == ""


def test_async_final_rescore_skips_when_suffix_no_longer_matches() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_ime_asr_events([ASREvent(type="final", text="第一段", source="streaming_final")])
    thread._handle_ime_asr_events([ASREvent(type="final", text="第二段", source="streaming_final")])
    thread._handle_ime_asr_events(
        [
            ASREvent(
                type="final",
                text="第一段修正",
                stable_text="第一段",
                source="final_rescore",
            )
        ]
    )

    assert ops == [
        ("sync", "第一段"),
        ("sync", "第一段第二段"),
    ]
    assert thread.committed_text == "第一段第二段"


def test_final_only_commits_remaining_text() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.committed_partial_text = "我们现在讨论"

    thread._handle_streaming_final(ASREvent(type="final", text="我们现在讨论流式输入的问题"))

    assert ops == [("sync", "流式输入的问题")]
    assert thread.committed_partial_text == ""
    assert thread.composition_text == ""


def test_final_without_committed_text_commits_full_text() -> None:
    ops = with_input_recorder()
    thread = make_thread()

    thread._handle_streaming_final(ASREvent(type="final", text="我们讨论流式输入"))

    assert ops == [("sync", "我们讨论流式输入")]
    assert thread.committed_partial_text == ""
    assert thread.composition_text == ""


def test_final_prefix_mismatch_skips_full_rewrite() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.committed_partial_text = "我们现在讨论"

    thread._handle_streaming_final(ASREvent(type="final", text="今天我们讨论流式输入的问题"))

    assert ops == []
    assert thread.committed_partial_text == ""
    assert thread.composition_text == ""


def test_empty_final_only_clears_composition_and_state() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.committed_partial_text = "我们现在讨论"
    thread.composition_text = "输入"

    thread._handle_streaming_final(ASREvent(type="final", text=""))

    assert ops == [("backspace", 2)]
    assert thread.committed_partial_text == ""
    assert thread.composition_text == ""


def test_final_after_long_partial_only_commits_tail() -> None:
    ops = with_input_recorder()
    thread = make_thread()
    thread.committed_partial_text = "我们现在讨论一下流式输入"

    thread._handle_streaming_final(ASREvent(type="final", text="我们现在讨论一下流式输入的问题"))

    assert ops == [("sync", "的问题")]
    assert thread.committed_partial_text == ""
    assert thread.composition_text == ""


def main() -> None:
    tests = [
        test_minimal_patch_append,
        test_minimal_patch_tail_correction,
        test_partial_long_text_segments_to_tail_composition,
        test_streaming_route_uses_virtual_input_session_for_partial,
        test_streaming_route_allows_virtual_prefix_revision,
        test_streaming_final_commits_virtual_text,
        test_virtual_input_window_resets_after_50_chars_without_deleting,
        test_async_final_rescore_replaces_matching_suffix,
        test_async_final_rescore_skips_when_suffix_no_longer_matches,
        test_final_only_commits_remaining_text,
        test_final_without_committed_text_commits_full_text,
        test_final_prefix_mismatch_skips_full_rewrite,
        test_empty_final_only_clears_composition_and_state,
        test_final_after_long_partial_only_commits_tail,
    ]
    for test in tests:
        test()
    print(f"ok - {len(tests)} streaming composition tests passed")


if __name__ == "__main__":
    main()
