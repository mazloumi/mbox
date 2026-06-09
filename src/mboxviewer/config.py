import os
from dataclasses import dataclass
from typing import Optional


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    mbox_path: str
    index_path: str
    archive_dir: str = "/archive"
    # Display name for the mbox (the host filename). The container mount renames the file
    # to /data/mail.mbox, so basename(mbox_path) loses the real name; this preserves it.
    mbox_name: str = ""
    host: str = "0.0.0.0"
    port: int = 9000
    # --- Assistant / semantic-search tiers (both opt-in, off by default) ---
    semantic_search_enabled: bool = False
    assistant_enabled: bool = False
    anthropic_api_key: Optional[str] = None
    gen_model: str = "claude-sonnet-4-6"
    embed_backend: str = "local"          # "local" | "ollama"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    ollama_url: str = "http://host.docker.internal:11434"

    def assistant_active(self) -> bool:
        """Chat tier is on only when explicitly enabled AND a key is present."""
        return bool(self.assistant_enabled and self.anthropic_api_key)

    def semantic_active(self) -> bool:
        """Retrieval tier; the assistant requires it, so it implies semantic."""
        return bool(self.semantic_search_enabled or self.assistant_active())


def load_settings() -> Settings:
    return Settings(
        mbox_path=os.environ.get("MBOX_PATH", "/data/mail.mbox"),
        index_path=os.environ.get("INDEX_PATH", "/index/index.db"),
        archive_dir=os.environ.get("ARCHIVE_DIR", "/archive"),
        mbox_name=os.environ.get("MBOX_NAME", ""),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "9000")),
        semantic_search_enabled=_flag(os.environ.get("SEMANTIC_SEARCH")),
        assistant_enabled=_flag(os.environ.get("ASSISTANT_ENABLED")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        gen_model=os.environ.get("ASSISTANT_MODEL", "claude-sonnet-4-6"),
        embed_backend=os.environ.get("EMBED_BACKEND", "local"),
        embed_model=os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
    )
