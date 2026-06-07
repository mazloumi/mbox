import re
import bleach

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p", "br", "div", "span", "img", "table", "thead", "tbody", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "u", "font",
]
ALLOWED_ATTRS = {
    "*": ["style", "align", "width", "height", "color"],
    "a": ["href", "title", "target"],
    "img": ["src", "alt", "width", "height"],
    "font": ["color", "face", "size"],
}

# Matches dangerous tags together with all their inner content (e.g. <script>...</script>).
# bleach with strip=True removes the tags but keeps text nodes inside them, so we must
# excise the entire element—including its content—before handing off to bleach.
_DANGEROUS_TAGS = re.compile(
    r'<(script|style|object|embed|applet|form|iframe|frame|frameset|link|meta|base)'
    r'[^>]*>.*?</\1>'
    r'|<(script|style|object|embed|applet|form|iframe|frame|frameset|link|meta|base)[^>]*/?>',
    re.IGNORECASE | re.DOTALL,
)

_REMOTE_SRC = re.compile(r'\ssrc\s*=\s*(["\'])\s*https?://[^"\']*\1', re.IGNORECASE)


def sanitize_html(html: str, allow_remote: bool = False) -> str:
    # Strip dangerous tags and their content before bleach so that script bodies
    # (e.g. "alert(1)") are not left as bare text nodes in the output.
    pre_stripped = _DANGEROUS_TAGS.sub("", html or "")
    cleaned = bleach.clean(
        pre_stripped, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    if not allow_remote:
        cleaned = _REMOTE_SRC.sub(' src=""', cleaned)
    return cleaned
