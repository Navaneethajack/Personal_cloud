"""
utils.py

Shared helper utilities:
    - Filename sanitization / path-traversal prevention
    - Human-readable size formatting
    - MIME type detection
    - File type -> icon mapping
    - Folder path normalization
    - Duplicate-name resolution helpers
"""

from __future__ import annotations

import mimetypes
import re
from typing import Iterable

# Characters that are not allowed in a single path segment (file or folder name).
_INVALID_CHARS_PATTERN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """
    Sanitize a single file or folder name segment.

    - Strips path separators and traversal sequences.
    - Removes control / invalid filesystem characters.
    - Collapses whitespace.
    - Falls back to a safe default if the result is empty.

    This function is meant to sanitize ONE path segment at a time (a file
    name or a single folder name), never a full multi-segment path.
    """
    if name is None:
        return "unnamed"

    name = name.strip()

    # Reject/strip any path traversal or separator sequences outright.
    name = name.replace("..", "")
    name = name.replace("/", "")
    name = name.replace("\\", "")

    # Remove other invalid characters.
    name = _INVALID_CHARS_PATTERN.sub("", name)

    # Collapse repeated whitespace.
    name = re.sub(r"\s+", " ", name).strip()

    # Strip leading dots/spaces (hidden-file / trailing-dot edge cases on some OSes).
    name = name.lstrip(".").strip()

    if not name:
        return "unnamed"

    # Limit segment length to something reasonable.
    if len(name) > 200:
        name = name[:200]

    return name


def normalize_folder_path(path: str) -> str:
    """
    Normalize a folder path made of '/'-separated segments.

    Each segment is sanitized individually via sanitize_name(). The result
    never contains '..' or leading/trailing slashes, and always uses '/'
    as the separator. An empty/root path normalizes to "".
    """
    if not path:
        return ""

    raw_segments = path.replace("\\", "/").split("/")
    clean_segments = [sanitize_name(seg) for seg in raw_segments if seg.strip() not in ("", ".", "..")]
    return "/".join(clean_segments)


def join_folder(parent: str, child: str) -> str:
    """Join a parent folder path with a single sanitized child folder name."""
    parent = normalize_folder_path(parent)
    child = sanitize_name(child)
    if not parent:
        return child
    return f"{parent}/{child}"


def build_object_key(folder: str, filename: str) -> str:
    """
    Build a safe B2 object key from a folder path and a filename.

    Folders are represented purely as key prefixes (no separate
    filesystem-style folder objects).
    """
    folder = normalize_folder_path(folder)
    filename = sanitize_name(filename)
    if folder:
        return f"{folder}/{filename}"
    return filename


def get_extension(filename: str) -> str:
    """Return the lowercase extension (without dot), or '' if none."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename; fall back to application/octet-stream."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def format_size(num_bytes: float) -> str:
    """Format a byte count as a human-readable string (e.g. '4.2 MB')."""
    if num_bytes is None:
        return "0 B"
    num_bytes = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = num_bytes
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


# Extension groups used purely for icon display and stats bucketing.
# NOTE: this is NOT a whitelist -- all file types are always accepted for
# upload regardless of extension. This mapping only controls which icon
# and category label is shown in the UI.
_ICON_MAP = {
    "pdf": "📄",
    "doc": "📝", "docx": "📝",
    "xls": "📊", "xlsx": "📊", "csv": "📊",
    "ppt": "📙", "pptx": "📙",
    "txt": "📃", "md": "📃",
    "zip": "📦", "rar": "📦", "7z": "📦", "tar": "📦", "gz": "📦",
    "jpg": "🖼", "jpeg": "🖼", "png": "🖼", "gif": "🖼", "webp": "🖼", "bmp": "🖼", "svg": "🖼",
    "mp3": "🎵", "wav": "🎵", "flac": "🎵", "aac": "🎵",
    "mp4": "🎬", "mkv": "🎬", "avi": "🎬", "mov": "🎬", "webm": "🎬",
    "apk": "📱",
    "exe": "⚙️", "msi": "⚙️",
    "iso": "💿",
    "json": "🔧", "xml": "🔧", "yaml": "🔧", "yml": "🔧",
    "py": "🐍", "js": "📜", "ts": "📜", "java": "☕", "c": "🔤", "cpp": "🔤", "html": "🌐", "css": "🎨",
}

_CATEGORY_MAP = {
    "pdf": "PDF",
    "doc": "Documents", "docx": "Documents", "txt": "Documents", "md": "Documents",
    "xls": "Spreadsheets", "xlsx": "Spreadsheets", "csv": "Spreadsheets",
    "ppt": "Presentations", "pptx": "Presentations",
    "zip": "Archives", "rar": "Archives", "7z": "Archives", "tar": "Archives", "gz": "Archives",
    "jpg": "Images", "jpeg": "Images", "png": "Images", "gif": "Images",
    "webp": "Images", "bmp": "Images", "svg": "Images",
    "mp3": "Audio", "wav": "Audio", "flac": "Audio", "aac": "Audio",
    "mp4": "Videos", "mkv": "Videos", "avi": "Videos", "mov": "Videos", "webm": "Videos",
}

FOLDER_ICON = "📁"
DEFAULT_FILE_ICON = "📄"
DEFAULT_CATEGORY = "Other"


def get_file_icon(filename: str) -> str:
    """Return an icon (emoji) appropriate for the file's extension."""
    ext = get_extension(filename)
    return _ICON_MAP.get(ext, DEFAULT_FILE_ICON)


def get_file_category(filename: str) -> str:
    """Return a broad category label for a file, used for storage statistics."""
    ext = get_extension(filename)
    return _CATEGORY_MAP.get(ext, DEFAULT_CATEGORY)


def unique_display_name(desired_name: str, existing_names: Iterable[str]) -> str:
    """
    Given a desired file name and a collection of existing names in the same
    folder, return a unique name by appending " (1)", " (2)", etc. before
    the extension if needed.
    """
    existing = set(existing_names)
    if desired_name not in existing:
        return desired_name

    if "." in desired_name:
        base, ext = desired_name.rsplit(".", 1)
        ext = f".{ext}"
    else:
        base, ext = desired_name, ""

    counter = 1
    while True:
        candidate = f"{base} ({counter}){ext}"
        if candidate not in existing:
            return candidate
        counter += 1
