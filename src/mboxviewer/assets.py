import hashlib
import io
import os
import re
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

# Conservative host substrings strongly associated with open-tracking. The reliable
# signal is the 1x1 dimension check in is_tracking_pixel; this list is a secondary catch
# for dimensionless trackers. We deliberately avoid broad substrings like "open." or
# "px." that would also match legitimate image hosts (a false positive silently drops a
# real image from the archive), favoring email-specific tracker domains and clear prefixes.
TRACKER_HOSTS = (
    "track.", "tracking.", "click.", "pixel.", "beacon.",
    "list-manage.com", "sendgrid.net", "mailgun.org", "sparkpostmail.com",
)


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def normalize_url(url):
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _is_remote(url):
    return url.startswith(("http://", "https://", "//"))


def _dim(value):
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


class _ImgRefParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        d = dict(attrs)
        src = d.get("src")
        if src and _is_remote(src):
            self.refs.append((normalize_url(src), _dim(d.get("width")), _dim(d.get("height"))))


def extract_image_refs(html):
    """Return [(normalized_url, width|None, height|None)] for remote <img> and CSS url()."""
    parser = _ImgRefParser()
    parser.feed(html or "")
    refs = list(parser.refs)
    for m in re.finditer(r'url\(\s*["\']?\s*((?:https?:)?//[^)"\']+)', html or "", re.IGNORECASE):
        refs.append((normalize_url(m.group(1)), None, None))
    return refs


def is_tracking_pixel(url, width, height):
    if (width is not None and width <= 2) or (height is not None and height <= 2):
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(t in host for t in TRACKER_HOSTS)


MAX_ASSET_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT = 10


@dataclass
class FetchResult:
    ok: bool
    content_type: str = None
    data: bytes = None
    error: str = None


def fetch_image(url, timeout=FETCH_TIMEOUT, max_bytes=MAX_ASSET_BYTES):
    """Download an image. Never raises; returns a FetchResult. Honors HTTP(S)_PROXY env."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mbox-viewer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return FetchResult(False, error=f"not an image: {ctype or 'unknown'}")
            buf = io.BytesIO()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    return FetchResult(False, error="too large")
            return FetchResult(True, content_type=ctype, data=buf.getvalue())
    except Exception as exc:  # noqa: BLE001 - any network/parse error is a failed fetch
        return FetchResult(False, error=str(exc))


def assets_dir(archive_dir):
    return os.path.join(archive_dir, "assets")


def asset_path(archive_dir, h):
    return os.path.join(assets_dir(archive_dir), h)


def write_asset_bytes(archive_dir, h, data):
    os.makedirs(assets_dir(archive_dir), exist_ok=True)
    with open(asset_path(archive_dir, h), "wb") as f:
        f.write(data)


def read_asset_bytes(archive_dir, h):
    try:
        with open(asset_path(archive_dir, h), "rb") as f:
            return f.read()
    except OSError:
        return None


def rewrite_cached_images(html, cached_hashes):
    """Replace remote <img src> and CSS url() whose url_hash is in cached_hashes with
    the local /api/asset/<hash> endpoint. Leaves uncached refs untouched."""
    if not html or not cached_hashes:
        return html or ""

    def repl_src(m):
        prefix, quote, url = m.group(1), m.group(2), m.group(3)
        h = url_hash(normalize_url(url))
        if h in cached_hashes:
            return f"{prefix}{quote}/api/asset/{h}{quote}"
        return m.group(0)

    def repl_css(m):
        h = url_hash(normalize_url(m.group(1)))
        if h in cached_hashes:
            return f'url("/api/asset/{h}")'
        return m.group(0)

    html = re.sub(r'(\ssrc\s*=\s*)(["\'])((?:https?:)?//[^"\']*)\2', repl_src, html, flags=re.IGNORECASE)
    html = re.sub(r'url\(\s*["\']?\s*((?:https?:)?//[^)"\']+)["\']?\s*\)', repl_css, html, flags=re.IGNORECASE)
    return html
