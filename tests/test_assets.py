from mboxviewer.assets import url_hash, normalize_url, extract_image_refs, is_tracking_pixel


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
