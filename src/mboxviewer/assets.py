import hashlib
import re
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
