from mboxviewer import assistant
from mboxviewer.retrieve import Snippet


def _snips():
    return [
        Snippet(7, "Roof leak", "bob@x.com", "2024-03-01", "water in the attic"),
        Snippet(9, "Invoice", "acme@x.com", "2024-03-05", "amount due $500"),
    ]


def test_context_block_labels_ids():
    block = assistant.build_context_block(_snips())
    assert "[#7]" in block and "[#9]" in block
    assert "water in the attic" in block
    assert "Roof leak" in block


def test_sources_payload():
    src = assistant.sources_for(_snips())
    assert src == [
        {"id": 7, "subject": "Roof leak", "from": "bob@x.com", "date": "2024-03-01"},
        {"id": 9, "subject": "Invoice", "from": "acme@x.com", "date": "2024-03-05"},
    ]


def test_iter_answer_sends_system_history_and_context():
    captured = {}

    def fake_generate(system, messages):
        captured["system"] = system
        captured["messages"] = messages
        yield "The roof "
        yield "leaked [#7]."

    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}]
    out = "".join(assistant.iter_answer(
        fake_generate, history, "what leaked?", _snips()))

    assert out == "The roof leaked [#7]."
    assert "only" in captured["system"].lower()           # grounding instruction
    assert captured["messages"][0] == {"role": "user", "content": "hi"}
    assert captured["messages"][1] == {"role": "assistant", "content": "hello"}
    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert "what leaked?" in last["content"]
    assert "[#7]" in last["content"]                       # context block appended


def test_iter_answer_no_snippets_says_not_found():
    seen = {}
    def fake_generate(system, messages):
        seen["last"] = messages[-1]["content"]
        yield "ok"
    list(assistant.iter_answer(fake_generate, [], "anything?", []))
    assert "anything?" in seen["last"]
    assert "no matching email" in seen["last"].lower()


class _FakeStream:
    def __init__(self, texts, final): self.text_stream = list(texts); self._final = final
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._final


class _Final:
    def __init__(self, stop_reason="end_turn", content=None):
        self.stop_reason = stop_reason
        self.content = content or []


def test_make_anthropic_generate_uses_streaming():
    class FakeMessages:
        def __init__(self): self.kwargs = None
        def stream(self, **kwargs):
            self.kwargs = kwargs
            return _FakeStream(["a", "b", "c"], _Final("end_turn"))

    class FakeClient:
        def __init__(self): self.messages = FakeMessages()

    client = FakeClient()
    gen = assistant.make_anthropic_generate(client, "claude-sonnet-4-6")
    out = "".join(gen("SYS", [{"role": "user", "content": "q"}]))
    assert out == "abc"
    assert client.messages.kwargs["model"] == "claude-sonnet-4-6"
    assert client.messages.kwargs["system"] == "SYS"
    assert client.messages.kwargs["max_tokens"] == 1024
    assert "tools" not in client.messages.kwargs   # no tools passed -> plain call


def test_make_anthropic_generate_runs_tool_then_answers():
    class ToolBlock:
        type = "tool_use"; id = "tu_1"; name = "query_attachments"
        input = {"category": "Media"}

    class FakeMessages:
        def __init__(self): self.calls = []
        def stream(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:   # first turn: narrate, then call the tool
                return _FakeStream(["Let me check. "],
                                   _Final("tool_use", [ToolBlock()]))
            return _FakeStream(["You have 2 videos [#5]."], _Final("end_turn"))

    class FakeClient:
        def __init__(self): self.messages = FakeMessages()

    ran = {}
    def run_tool(name, inp):
        ran["name"] = name; ran["input"] = inp
        return '{"total_matches": 2}'

    client = FakeClient()
    gen = assistant.make_anthropic_generate(
        client, "m", tools=[assistant.ATTACHMENT_TOOL], run_tool=run_tool)
    out = "".join(gen("SYS", [{"role": "user", "content": "how many videos?"}]))

    assert out == "Let me check. \n\nYou have 2 videos [#5]."   # narration separated from answer
    assert ran == {"name": "query_attachments", "input": {"category": "Media"}}
    assert "tools" in client.messages.calls[0]
    # the tool result was fed back on the second turn
    second = client.messages.calls[1]["messages"]
    assert second[-1]["role"] == "user"
    tr = second[-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu_1"
    assert tr["content"] == '{"total_matches": 2}'
