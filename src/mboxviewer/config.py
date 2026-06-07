import os
from dataclasses import dataclass


@dataclass
class Settings:
    mbox_path: str
    index_path: str
    archive_dir: str = "/archive"
    host: str = "0.0.0.0"
    port: int = 9000


def load_settings() -> Settings:
    return Settings(
        mbox_path=os.environ.get("MBOX_PATH", "/data/mail.mbox"),
        index_path=os.environ.get("INDEX_PATH", "/index/index.db"),
        archive_dir=os.environ.get("ARCHIVE_DIR", "/archive"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "9000")),
    )
