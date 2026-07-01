from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server


def with_input_recorder():
    ops: list[tuple[str, object]] = []
    server.type_text = lambda text: ops.append(("type", text))
    server.press_key = lambda vk: ops.append(("key", vk))
    return ops


def test_spoken_punctuation_is_consumed_as_events() -> None:
    ops = with_input_recorder()
    session = server.FlowInputSession()
    settings = server.BridgeSettings(
        filter_punctuation=True,
        convert_spoken_punctuation=True,
        enable_voice_commands=True,
    )

    session.sync_state("你好逗号世界句号", settings)

    assert ops == [
        ("type", "你好"),
        ("type", "，"),
        ("type", "世界"),
        ("type", "。"),
    ]
    assert session.raw_session_start == len("你好逗号世界句号")
    assert session.text_session.text == ""


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
        ("type", "hello"),
        ("type", ","),
        ("type", "world"),
    ]


def main() -> None:
    tests = [
        test_spoken_punctuation_is_consumed_as_events,
        test_spoken_punctuation_keeps_style_selection,
    ]
    for test in tests:
        test()
    print(f"ok - {len(tests)} spoken punctuation command tests passed")


if __name__ == "__main__":
    main()
