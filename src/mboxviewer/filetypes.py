import os

CATEGORY_ORDER = [
    "Documents", "Spreadsheets", "Presentations", "Images",
    "Archives", "Calendar", "Contacts", "Media", "Other",
]

_DOCUMENTS = {
    "application/pdf", "application/msword", "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
}
_SPREADSHEETS = {
    "application/vnd.ms-excel",  # text/csv is handled by the explicit early check
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
}
_PRESENTATIONS = {
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
}
_ARCHIVES = {
    "application/zip", "application/x-zip-compressed", "application/gzip",
    "application/x-gzip", "application/x-tar", "application/x-rar-compressed",
    "application/vnd.rar", "application/x-7z-compressed",
}
# Keep in sync with extract._CALENDAR_MIMES (separate module, no shared import).
_CALENDAR = {"text/calendar", "application/ics", "text/x-vcalendar"}
# Keep in sync with extract._VCARD_MIMES (separate module, no shared import).
_CONTACTS = {"text/x-vcard", "text/vcard", "application/vcard", "text/directory"}


_EXT_CATEGORY = {}
for _cat, _exts in {
    "Documents": ".pdf .doc .docx .rtf .odt .txt .md .pages .docm",
    "Spreadsheets": ".xls .xlsx .ods .numbers .xlsm",
    "Presentations": ".ppt .pptx .pps .ppsx .odp .key .pptm",
    "Images": ".jpg .jpeg .png .gif .bmp .webp .tif .tiff .heic .heif",
    "Archives": ".zip .rar .7z .gz .tar .bz2 .tgz .jar",
    "Calendar": ".ics .vcs",
    "Contacts": ".vcf",
    "Media": (".mp3 .m4a .wav .aac .ogg .oga .flac .opus .wma "
              ".mp4 .m4v .mov .avi .wmv .mkv .webm .mpg .mpeg .3gp"),
}.items():
    for _e in _exts.split():
        _EXT_CATEGORY[_e] = _cat
del _cat, _exts, _e


def category_for_mime(mime):
    m = (mime or "").lower().split(";")[0].strip()
    if m.startswith("image/"):
        return "Images"
    if m.startswith("audio/") or m.startswith("video/"):
        return "Media"
    if m in _CALENDAR:
        return "Calendar"
    if m in _CONTACTS:
        return "Contacts"
    if m == "text/csv":
        return "Spreadsheets"
    if m in _DOCUMENTS:
        return "Documents"
    if m in _SPREADSHEETS:
        return "Spreadsheets"
    if m in _PRESENTATIONS:
        return "Presentations"
    if m in _ARCHIVES:
        return "Archives"
    if m.startswith("text/"):
        return "Documents"
    return "Other"


def category_for(mime, filename):
    """Category from MIME, falling back to the filename extension when the MIME
    is generic/unknown (maps to 'Other')."""
    cat = category_for_mime(mime)
    if cat != "Other":
        return cat
    ext = os.path.splitext(filename or "")[1].lower()
    return _EXT_CATEGORY.get(ext, "Other")
