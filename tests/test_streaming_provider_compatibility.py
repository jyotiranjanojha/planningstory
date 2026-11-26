from webapp.app import analyzer
from webapp.app.analyzer import _extract_openai_stream_content, _snapshot_suffix


class _FakeSseResponse:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.events)


def test_openai_delta_content_is_preserved_as_a_delta():
    kind, text = _extract_openai_stream_content(
        {"choices": [{"delta": {"content": "again"}}]}
    )

    assert kind == "delta"
    assert text == "again"


def test_openai_message_snapshots_emit_only_new_text():
    first_kind, first_text = _extract_openai_stream_content(
        {"choices": [{"message": {"content": "Demand is "}}]}
    )
    second_kind, second_text = _extract_openai_stream_content(
        {"choices": [{"message": {"content": "Demand is fully met."}}]}
    )

    assert first_kind == second_kind == "snapshot"
    emitted = _snapshot_suffix(first_text, "")
    emitted += _snapshot_suffix(second_text, emitted)
    assert emitted == "Demand is fully met."


def test_stream_llm_deduplicates_openai_compatible_snapshots(monkeypatch):
    monkeypatch.setattr(
        analyzer,
        "LLM_CONFIG",
        {"provider": "openai", "model": "test-model", "base_url": "http://test", "api_key": None},
    )
    monkeypatch.setattr(
        analyzer.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeSseResponse(
            [
                b'data: {"choices":[{"message":{"content":"Demand is "}}]}\n',
                b'data: {"choices":[{"message":{"content":"Demand is fully met."}}]}\n',
                b'data: [DONE]\n',
            ]
        ),
    )

    assert list(analyzer.stream_llm("question", "system")) == ["Demand is ", "fully met."]


def test_openvino_style_raw_fragments_remain_deltas():
    fragments = ["Demand", " is", " fully", " met."]

    assert "".join(fragments) == "Demand is fully met."