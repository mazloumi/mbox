"""Assistant tier: assemble a grounded prompt from retrieved snippets and stream
a cited answer from Claude. The SDK call is isolated in make_anthropic_generate so
the rest is pure and testable without a network."""
from typing import Callable, Dict, Iterator, List, Optional

from .retrieve import Snippet

SYSTEM_PROMPT = (
    "You are an assistant answering questions about the user's own email archive. "
    "Answer ONLY from the email context provided in the user's message and from the "
    "results of the query_attachments tool. "
    "For any question about which files or attachments exist — to list, count, or find "
    "audio, video, images, documents, spreadsheets, etc. — use the query_attachments "
    "tool rather than relying on the snippets, which cover only a few messages. "
    "Cite every claim inline as [#<id>], where <id> is the integer message id shown for "
    "each snippet or returned by the tool (e.g. [#42]); use only ids you have seen. "
    "If the answer is not in the provided email or tool results, say so plainly rather "
    "than guessing. Be concise."
)

# Tool: lets Claude query the attachment catalog directly (the same data the Files
# tab reads), so file questions match the Files tab instead of relying on text search.
ATTACHMENT_TOOL = {
    "name": "query_attachments",
    "description": (
        "Query the user's complete attachment catalog (every file attached to any email) "
        "by category, filename, and/or sender. Use this for any question about which files "
        "exist — to list, count, or locate audio, video, images, documents, spreadsheets, "
        "and so on. Returns the total number of matches, a 'type_counts' breakdown of the "
        "full match set by type (e.g. how many are audio vs video — use this for exact "
        "counts, not the sample), and a sample of files, each with its message id (cite as "
        "[#id]), filename, type, size, subject, sender, and date."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["Documents", "Spreadsheets", "Presentations", "Images",
                         "Archives", "Enclosures", "Signatures", "Calendar",
                         "Contacts", "Media", "Other"],
                "description": ("File category. 'Media' covers BOTH audio and video files "
                                "(check each returned file's 'type' to tell them apart). "
                                "Omit to search across all categories."),
            },
            "filename_contains": {
                "type": "string",
                "description": "Case-insensitive substring to match in the filename.",
            },
            "sender_contains": {
                "type": "string",
                "description": "Case-insensitive substring to match in the sender (From).",
            },
            "limit": {
                "type": "integer",
                "description": ("Max files to return in the sample (default 200, max 500). "
                                "When the user wants the full list, pass a limit at least as "
                                "large as total_matches so you can list them all."),
            },
        },
    },
}


def build_context_block(snippets: List[Snippet]) -> str:
    parts = []
    for s in snippets:
        header = " · ".join(p for p in (s.subject, s.from_addr, s.date) if p)
        parts.append(f"[#{s.message_id}] {header}\n{s.text}")
    return "\n\n".join(parts)


def sources_for(snippets: List[Snippet]) -> List[Dict]:
    return [{"id": s.message_id, "subject": s.subject,
             "from": s.from_addr, "date": s.date} for s in snippets]


def iter_answer(generate: Callable[[str, List[Dict]], Iterator[str]],
                history: List[Dict], question: str,
                snippets: List[Snippet]) -> Iterator[str]:
    """Stream the answer text. `generate(system, messages)` yields text chunks."""
    context = build_context_block(snippets)
    user_turn = (
        f"{question}\n\n"
        f"Context — email snippets (cite by the [#id] shown):\n\n{context}"
        if snippets else
        f"{question}\n\n(No matching email was found in the archive.)"
    )
    messages = list(history) + [{"role": "user", "content": user_turn}]
    for chunk in generate(SYSTEM_PROMPT, messages):
        yield chunk


# Generous output ceiling: a request like "list all 130 audio files" produces a long
# table that 1024 tokens truncated mid-way (~24 rows).
MAX_TOKENS = 8192


def make_anthropic_generate(client, model: str, tools: Optional[List[Dict]] = None,
                            run_tool: Optional[Callable[[str, Dict], str]] = None,
                            max_rounds: int = 6):
    """Wrap an anthropic client into a generate(system, messages) -> iterator[str].

    When `tools` and `run_tool` are given, drive a streaming tool-use loop: each
    turn streams its text; if it ends in a tool call, run `run_tool(name, input)`,
    feed the result back, and continue until the model answers (or `max_rounds`)."""
    def generate(system: str, messages: List[Dict]) -> Iterator[str]:
        convo = list(messages)
        for round_no in range(max_rounds):
            kwargs = dict(model=model, max_tokens=MAX_TOKENS, system=system, messages=convo)
            if tools:
                kwargs["tools"] = tools
            emitted = False
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    emitted = emitted or bool(text)
                    yield text
                final = stream.get_final_message()
            if not run_tool or getattr(final, "stop_reason", None) != "tool_use":
                return
            if emitted:   # separate this turn's narration from the next turn's text
                yield "\n\n"
            convo.append({"role": "assistant", "content": final.content})
            results = []
            for block in final.content:
                if getattr(block, "type", None) == "tool_use":
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": run_tool(block.name, block.input)})
            convo.append({"role": "user", "content": results})
    return generate
