import threading

from voice_ask import VoiceAskManager


def test_stop_keeps_strip_visible_while_request_runs():
    strips = []
    results = []
    request_started = threading.Event()
    request_continue = threading.Event()
    manager = VoiceAskManager(on_strip_state=strips.append, on_result_state=results.append)
    manager.start(source="mobile")
    manager.set_prompt("问题")

    def fake_request(prompt, model):
        request_started.set()
        request_continue.wait(timeout=2)
        return "答案"

    manager._request_qwen = fake_request
    manager.stop_and_submit()

    assert strips[-1]["status"] == "thinking"
    assert strips[-1]["prompt"] == "问题"
    assert results[-1]["status"] == "thinking"
    assert request_started.wait(timeout=1)
    request_continue.set()


def test_editable_strip_text_becomes_submitted_prompt():
    manager = VoiceAskManager()
    manager.start(source="mobile")
    manager.set_prompt("typed and dictated prompt")

    assert manager.snapshot()["prompt"] == "typed and dictated prompt"


def test_copying_completed_result_hides_strip_and_result():
    strips = []
    results = []
    manager = VoiceAskManager(on_strip_state=strips.append, on_result_state=results.append)
    manager.status = "completed"
    manager.answer = "answer"
    manager.result_visible = True

    assert manager.result_for_copy() == "answer"
    assert strips[-1]["status"] == "hidden"
    assert results[-1]["resultVisible"] is False
    assert manager.snapshot()["status"] == "idle"
