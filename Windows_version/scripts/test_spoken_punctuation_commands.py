from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server

server.LOGGER.disabled = True


def with_input_recorder():
    ops: list[tuple[str, object]] = []
    server.type_text = lambda text: ops.append(("type", text))
    server.press_key = lambda vk: ops.append(("key", vk))
    return ops


def test_spoken_punctuation_stays_in_replaceable_text() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        convert_spoken_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("你好逗号世界句号", settings)

    assert ops == [("type", "你好，世界。")]
    assert session.raw_session_start == 0
    assert session.text_session.text == "你好，世界。"


def test_spoken_punctuation_keeps_style_selection() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        convert_spoken_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("hello逗号world", settings)

    assert ops == [
        ("type", "hello, world"),
    ]


def test_final_ime_punctuation_does_not_shift_spoken_command() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        convert_spoken_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("我今天要做这个逗号", settings)
    session.sync_state("我今天要做这个。逗号。还要做这个。", settings)

    assert ops == [
        ("type", "我今天要做这个，"),
        ("type", "还要做这个"),
    ]
    assert session.raw_session_start == 0
    assert session.text_session.text == "我今天要做这个，还要做这个"


def test_final_text_rolls_back_extra_character_after_spoken_punctuation() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        convert_spoken_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("你好逗号号", settings)
    session.sync_state("你好。逗号。世界。", settings)

    assert ops == [
        ("type", "你好，号"),
        ("key", server.VK_BACK),
        ("type", "世界"),
    ]
    assert session.text_session.text == "你好，世界"


def test_enter_cursor_follows_space_inserted_before_command() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(enable_voice_commands=True)

    session.sync_state("发送这条消息enter", settings)
    session.sync_state("发送这条消息 enter", settings)

    assert ops == [
        ("type", "发送这条消息"),
        ("key", server.VK_RETURN),
    ]
    assert session.raw_session_start == len("发送这条消息 enter")
    assert session.text_session.text == ""


def test_enter_cursor_follows_punctuation_around_command() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("发送这条消息enter", settings)
    session.sync_state("发送这条消息。enter。", settings)

    assert ops == [
        ("type", "发送这条消息"),
        ("key", server.VK_RETURN),
    ]
    assert session.raw_session_start == len("发送这条消息。enter")
    assert session.text_session.text == ""


def test_text_appended_after_enter_remains_active() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(enable_voice_commands=True)

    session.sync_state("发送这条消息enter", settings)
    session.sync_state("发送这条消息 enter下一条", settings)

    assert ops == [
        ("type", "发送这条消息"),
        ("key", server.VK_RETURN),
        ("type", "下一条"),
    ]
    assert session.text_session.text == "下一条"


def main() -> None:
    tests = [
        test_spoken_punctuation_stays_in_replaceable_text,
        test_spoken_punctuation_keeps_style_selection,
        test_final_ime_punctuation_does_not_shift_spoken_command,
        test_final_text_rolls_back_extra_character_after_spoken_punctuation,
        test_enter_cursor_follows_space_inserted_before_command,
        test_enter_cursor_follows_punctuation_around_command,
        test_text_appended_after_enter_remains_active,
    ]
    for test in tests:
        test()
    print(f"ok - {len(tests)} spoken punctuation command tests passed")


if __name__ == "__main__":
    main()
