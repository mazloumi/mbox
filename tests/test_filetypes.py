from mboxviewer.filetypes import category_for_mime, CATEGORY_ORDER


def test_categories():
    cases = {
        "application/pdf": "Documents",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Documents",
        "text/plain": "Documents",
        "text/csv": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
        "image/png": "Images",
        "image/jpeg": "Images",
        "application/zip": "Archives",
        "text/calendar": "Calendar",
        "audio/mpeg": "Media",
        "video/mp4": "Media",
        "application/octet-stream": "Other",
        "": "Other",
        None: "Other",
    }
    for mime, expected in cases.items():
        assert category_for_mime(mime) == expected, mime


def test_mime_params_stripped_and_case_insensitive():
    assert category_for_mime("IMAGE/PNG; name=x.png") == "Images"


def test_every_result_is_in_category_order():
    for mime in ["application/pdf", "image/png", "audio/x", "application/zip", "x/y"]:
        assert category_for_mime(mime) in CATEGORY_ORDER


def test_calendar_mimes():
    for m in ["text/calendar", "application/ics", "text/x-vcalendar"]:
        assert category_for_mime(m) == "Calendar", m


def test_contacts_mimes():
    from mboxviewer.filetypes import CATEGORY_ORDER
    for m in ["text/x-vcard", "text/vcard", "application/vcard", "text/directory"]:
        assert category_for_mime(m) == "Contacts", m
    assert "Contacts" in CATEGORY_ORDER


def test_category_for_extension_fallback():
    from mboxviewer.filetypes import category_for
    cases = {
        ("application/octet-stream", "x.pdf"): "Documents",
        ("application/x-pdf", "x.pdf"): "Documents",
        ("application/force-download", "report.doc"): "Documents",
        ("application/octet-stream", "s.xlsx"): "Spreadsheets",
        ("application/octet-stream", "d.pps"): "Presentations",
        ("application/octet-stream", "a.mp3"): "Media",
        ("application/octet-stream", "v.wmv"): "Media",
        ("application/octet-stream", "c.ics"): "Calendar",
        ("application/octet-stream", "k.vcf"): "Contacts",
        ("application/octet-stream", "p.jpg"): "Images",
        ("application/octet-stream", "a.zip"): "Archives",
    }
    for (mime, fn), expected in cases.items():
        assert category_for(mime, fn) == expected, (mime, fn)


def test_category_for_real_mime_wins_and_unknown():
    from mboxviewer.filetypes import category_for
    assert category_for("application/pdf", "x.bin") == "Documents"     # real mime wins
    assert category_for("application/octet-stream", "x.dat") == "Other"  # unknown ext
    assert category_for("application/octet-stream", "") == "Other"
