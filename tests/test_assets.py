from mboxviewer.assets import url_hash, normalize_url, extract_image_refs, is_tracking_pixel
from mboxviewer.assets import fetch_image, write_asset_bytes, read_asset_bytes, assets_dir, asset_path


def test_url_hash_stable_and_hex():
    h = url_hash("https://x.example/a.png")
    assert h == url_hash("https://x.example/a.png")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_normalize_protocol_relative():
    assert normalize_url("//host/a.png") == "https://host/a.png"
    assert normalize_url("https://h/a.png") == "https://h/a.png"


def test_extract_image_refs_img_and_css():
    html = ('<img src="https://a.example/1.png" width="120" height="80">'
            '<img src="//b.example/2.png">'
            '<img src="cid:embedded">'
            '<div style="background:url(https://c.example/3.png)"></div>')
    refs = extract_image_refs(html)
    urls = [u for (u, w, h) in refs]
    assert "https://a.example/1.png" in urls
    assert "https://b.example/2.png" in urls
    assert "https://c.example/3.png" in urls
    assert all("cid:" not in u for u in urls)
    a = next(r for r in refs if r[0] == "https://a.example/1.png")
    assert a[1] == 120 and a[2] == 80


def test_is_tracking_pixel():
    assert is_tracking_pixel("https://x/p.gif", 1, 1) is True
    assert is_tracking_pixel("https://x/p.gif", 2, 600) is True
    assert is_tracking_pixel("https://track.example/o.gif", None, None) is True
    assert is_tracking_pixel("https://x.example/logo.png", 300, 100) is False
    assert is_tracking_pixel("https://x.example/logo.png", None, None) is False


def test_fetch_image_success(image_server):
    base, requested = image_server
    res = fetch_image(f"{base}/logo.png")
    assert res.ok and res.content_type == "image/png" and res.data == b"FAKEIMAGEBYTES"
    assert "/logo.png" in requested


def test_fetch_image_rejects_non_image(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/notimage.html")
    assert res.ok is False and "image" in res.error


def test_fetch_image_rejects_svg(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/svg.svg")  # image/svg+xml can carry scripts
    assert res.ok is False and "unsafe" in res.error


def test_fetch_image_rejects_oversize(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/big.png", max_bytes=1024)
    assert res.ok is False and "large" in res.error


def test_fetch_image_network_error_is_caught():
    res = fetch_image("http://127.0.0.1:9/none.png", timeout=1)
    assert res.ok is False and res.error


def test_asset_byte_cache_roundtrip(tmp_path):
    archive_dir = str(tmp_path / "arch")
    write_asset_bytes(archive_dir, "abc123", b"hello")
    assert read_asset_bytes(archive_dir, "abc123") == b"hello"
    assert read_asset_bytes(archive_dir, "missing") is None
    assert assets_dir(archive_dir).endswith("assets")
    assert asset_path(archive_dir, "abc123").endswith("assets/abc123")


from mboxviewer.assets import rewrite_cached_images


def test_rewrite_replaces_only_cached():
    cached_url = "https://a.example/cached.png"
    uncached_url = "https://b.example/uncached.png"
    h = url_hash(cached_url)
    html = (f'<img src="{cached_url}">'
            f'<img src="{uncached_url}">'
            f'<div style="background:url({cached_url})"></div>')
    out = rewrite_cached_images(html, {h})
    assert f'/api/asset/{h}' in out
    assert cached_url not in out
    assert uncached_url in out


def test_rewrite_handles_protocol_relative():
    url = "//c.example/p.png"
    h = url_hash(normalize_url(url))
    out = rewrite_cached_images(f'<img src="{url}">', {h})
    assert f'/api/asset/{h}' in out


def test_rewrite_noop_when_nothing_cached():
    html = '<img src="https://a.example/x.png">'
    assert rewrite_cached_images(html, set()) == html


def test_entity_encoded_query_url_is_canonical():
    # Valid HTML encodes & as &amp; in attributes. Both extraction and rewrite must
    # hash the DECODED url so a cached CDN image (with query params) gets rewritten.
    raw = '<img src="https://cdn.example/i.png?w=800&amp;h=600">'
    decoded = "https://cdn.example/i.png?w=800&h=600"
    assert extract_image_refs(raw)[0][0] == decoded
    h = url_hash(decoded)
    out = rewrite_cached_images(raw, {h})
    assert f"/api/asset/{h}" in out and "&amp;" not in out
