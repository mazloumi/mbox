"""Assistant tier: assemble a grounded prompt from retrieved snippets and stream
a cited answer from Claude. The SDK call is isolated in make_anthropic_generate so
the rest is pure and testable without a network."""
from typing import Callable, Dict, Iterator, List

from .retrieve import Snippet

SYSTEM_PROMPT = (
    "You are an assistant answering questions about the user's own email archive. "
    "Answer ONLY from the email context provided in the user's message. "
    "Cite every claim inline as [#<id>], where <id> is the integer message id shown "
    "for each snippet (e.g. [#42]); use only ids present in the context. "
    "If the answer is not in the provided email, say so plainly rather than guessing. "
    "Be concise."
)


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


def make_anthropic_generate(client, model: str):
    """Wrap an anthropic client into a generate(system, messages) -> iterator[str]."""
    def generate(system: str, messages: List[Dict]) -> Iterator[str]:
        with client.messages.stream(
                model=model, max_tokens=1024, system=system, messages=messages) as stream:
            for text in stream.text_stream:
                yield text
    return generate
