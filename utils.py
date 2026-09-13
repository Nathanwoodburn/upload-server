import mimetypes
import os
import re
import secrets
import string
from datetime import UTC, datetime, timedelta

from flask import Request

RESERVED_SLUGS = {
    "assets",
    "static",
    "api",
    "bashrc",
    "delete",
    "modify",
    "d",
    "m",
    "u",
    "list",
    "mine",
    "favicon.ico",
    "favicon.png",
    "upload",
    ".well-known",
}

DEFAULT_EXPIRY_DAYS = int(os.getenv("DEFAULT_EXPIRY_DAYS", "7"))
MAX_EXPIRY_DAYS = int(os.getenv("MAX_EXPIRY_DAYS", "365"))


def parse_expiry(expiry_str: str | None) -> datetime:
    """
    Parses an expiry string into a UTC datetime.
    Defaults to 7 days if None or empty.
    Supports formats like '30m', '2h', '7d', '2w', '1mth', '1y', '7', or ISO date strings.
    """
    now = datetime.now(UTC)
    if not expiry_str or not expiry_str.strip():
        return now + timedelta(days=DEFAULT_EXPIRY_DAYS)

    clean_str = expiry_str.strip().lower()

    # If it's just digits, treat as days
    if clean_str.isdigit():
        days = int(clean_str)
        days = max(0, min(days, MAX_EXPIRY_DAYS))
        return now + timedelta(days=days if days > 0 else DEFAULT_EXPIRY_DAYS)

    # Match number + unit pattern, e.g. "30m", "2h", "7d", "2w", "1y"
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-z]+)$", clean_str)
    if match:
        val = float(match.group(1))
        unit = match.group(2)

        if unit in ("s", "sec", "second", "seconds"):
            delta = timedelta(seconds=val)
        elif unit in ("m", "min", "minute", "minutes"):
            delta = timedelta(minutes=val)
        elif unit in ("h", "hr", "hour", "hours"):
            delta = timedelta(hours=val)
        elif unit in ("d", "day", "days"):
            delta = timedelta(days=val)
        elif unit in ("w", "wk", "week", "weeks"):
            delta = timedelta(weeks=val)
        elif unit in ("mth", "month", "months"):
            delta = timedelta(days=val * 30)
        elif unit in ("y", "yr", "year", "years"):
            delta = timedelta(days=val * 365)
        else:
            delta = timedelta(days=DEFAULT_EXPIRY_DAYS)

        max_delta = timedelta(days=MAX_EXPIRY_DAYS)
        delta = min(delta, max_delta)
        min_delta = timedelta(seconds=30)
        delta = max(delta, min_delta)

        return now + delta

    # Try ISO date format parsing
    try:
        dt = datetime.fromisoformat(clean_str.replace("z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass

    # Fallback to default
    return now + timedelta(days=DEFAULT_EXPIRY_DAYS)


def format_size(size_bytes: int) -> str:
    """Formats bytes to human-readable string like '1.2 MB'."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        size_bytes /= 1024.0
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
    return f"{size_bytes:.1f} PB"


def format_relative_time(target_dt: datetime) -> str:
    """Formats time until target_dt in a human-friendly way."""
    now = datetime.now(UTC)
    if target_dt <= now:
        return "expired"

    diff = target_dt - now
    total_seconds = int(diff.total_seconds())

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes}m")

    if not parts:
        return "less than 1m"
    return " ".join(parts[:2])


def generate_slug(length: int = 6) -> str:
    """Generates a random URL-friendly slug."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_valid_slug(slug: str) -> bool:
    """Validates whether a slug is safe and does not clash with system routes."""
    if not slug or len(slug) > 64:
        return False
    # Allow alphanumeric, underscore, hyphen, and dot (not starting or ending with dot)
    if not re.match(r"^[a-zA-Z0-9_\-]+(\.[a-zA-Z0-9_\-]+)*$", slug):
        return False
    return slug.lower() not in RESERVED_SLUGS


def generate_token(prefix: str = "") -> str:
    """Generates a secure random token."""
    token = secrets.token_urlsafe(18)
    return f"{prefix}{token}" if prefix else token


def get_user_token(request: Request) -> tuple[str, bool]:
    """
    Extracts the user token from headers, cookies, or query params.
    Returns (user_token, is_new).
    """
    # 1. Header (CLI / curl / machine uuid)
    token = (
        request.headers.get("X-User-Token")
        or request.headers.get("X-Machine-Id")
        or request.headers.get("X-Token")
    )
    if token and token.strip():
        return token.strip(), False

    # 2. Cookie (Browser)
    cookie_token = request.cookies.get("user_token")
    if cookie_token and cookie_token.strip():
        return cookie_token.strip(), False

    # 3. Query string
    query_token = request.args.get("user_token") or request.args.get("token")
    if query_token and query_token.strip():
        return query_token.strip(), False

    # 4. Generate a new one
    new_token = generate_token("usr_")
    return new_token, True


def get_base_url(request: Request) -> str:
    """Returns the effective base URL including scheme and host."""
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}".rstrip("/")


LANGUAGE_MAP = {
    # Scripting & Programming Languages
    ".lua": ("lua", "Lua Script"),
    ".py": ("python", "Python Script"),
    ".pyw": ("python", "Python Script"),
    ".js": ("javascript", "JavaScript"),
    ".mjs": ("javascript", "JavaScript Module"),
    ".cjs": ("javascript", "CommonJS"),
    ".ts": ("typescript", "TypeScript"),
    ".tsx": ("tsx", "React TypeScript"),
    ".jsx": ("jsx", "React JSX"),
    ".sh": ("bash", "Shell Script"),
    ".bash": ("bash", "Bash Script"),
    ".zsh": ("bash", "Zsh Script"),
    ".fish": ("bash", "Fish Script"),
    ".c": ("c", "C Source"),
    ".h": ("c", "C Header"),
    ".cpp": ("cpp", "C++ Source"),
    ".hpp": ("cpp", "C++ Header"),
    ".cc": ("cpp", "C++ Source"),
    ".cxx": ("cpp", "C++ Source"),
    ".hh": ("cpp", "C++ Header"),
    ".rs": ("rust", "Rust Source"),
    ".go": ("go", "Go Source"),
    ".java": ("java", "Java Source"),
    ".kt": ("kotlin", "Kotlin Source"),
    ".kts": ("kotlin", "Kotlin Script"),
    ".swift": ("swift", "Swift Source"),
    ".rb": ("ruby", "Ruby Script"),
    ".php": ("php", "PHP Script"),
    ".cs": ("csharp", "C# Source"),
    ".r": ("r", "R Script"),
    ".dart": ("dart", "Dart Source"),
    ".scala": ("scala", "Scala Source"),
    ".pl": ("perl", "Perl Script"),
    ".pm": ("perl", "Perl Module"),
    ".ex": ("elixir", "Elixir Source"),
    ".exs": ("elixir", "Elixir Script"),
    ".erl": ("erlang", "Erlang Source"),
    ".hs": ("haskell", "Haskell Source"),
    ".zig": ("zig", "Zig Source"),
    ".nim": ("nim", "Nim Source"),
    ".v": ("v", "V Source"),
    ".sv": ("systemverilog", "SystemVerilog Source"),
    ".vhd": ("vhdl", "VHDL Source"),
    ".vhdl": ("vhdl", "VHDL Source"),
    ".asm": ("nasm", "Assembly Source"),
    ".s": ("nasm", "Assembly Source"),
    ".bat": ("batch", "Batch Script"),
    ".cmd": ("batch", "Batch Script"),
    ".ps1": ("powershell", "PowerShell Script"),
    ".psm1": ("powershell", "PowerShell Module"),
    ".vim": ("vim", "Vim Script"),
    ".vue": ("javascript", "Vue Component"),
    ".svelte": ("javascript", "Svelte Component"),
    ".sol": ("solidity", "Solidity Contract"),
    ".awk": ("awk", "AWK Script"),
    ".sed": ("sed", "Sed Script"),
    ".nix": ("nix", "Nix Expression"),
    ".cmake": ("cmake", "CMake Script"),
    # Web & Markup & Styling
    ".html": ("html", "HTML Document"),
    ".htm": ("html", "HTML Document"),
    ".css": ("css", "CSS Stylesheet"),
    ".scss": ("scss", "SCSS Stylesheet"),
    ".sass": ("sass", "Sass Stylesheet"),
    ".less": ("less", "Less Stylesheet"),
    ".xml": ("xml", "XML Document"),
    ".md": ("markdown", "Markdown Document"),
    ".markdown": ("markdown", "Markdown Document"),
    ".rst": ("rest", "reStructuredText Document"),
    ".asciidoc": ("asciidoc", "AsciiDoc Document"),
    ".adoc": ("asciidoc", "AsciiDoc Document"),
    ".tex": ("latex", "LaTeX Document"),
    # Data & Config formats
    ".json": ("json", "JSON Data"),
    ".jsonc": ("json", "JSON with Comments"),
    ".yaml": ("yaml", "YAML Document"),
    ".yml": ("yaml", "YAML Document"),
    ".toml": ("toml", "TOML Configuration"),
    ".ini": ("ini", "INI Configuration"),
    ".cfg": ("ini", "Config File"),
    ".conf": ("ini", "Config File"),
    ".properties": ("ini", "Properties File"),
    ".sql": ("sql", "SQL Script"),
    ".dockerfile": ("docker", "Dockerfile"),
    ".diff": ("diff", "Diff / Patch"),
    ".patch": ("diff", "Patch File"),
    ".proto": ("protobuf", "Protocol Buffer"),
    ".graphql": ("graphql", "GraphQL Schema"),
    ".gql": ("graphql", "GraphQL Schema"),
    ".tf": ("hcl", "Terraform Config"),
    ".hcl": ("hcl", "HCL Config"),
    # Plain text & Logs & Tabular
    ".txt": ("none", "Plain Text"),
    ".log": ("none", "Log File"),
    ".csv": ("none", "CSV Spreadsheet"),
    ".tsv": ("none", "TSV Spreadsheet"),
    ".env": ("bash", "Environment Config"),
}

SPECIAL_FILENAMES = {
    "dockerfile": ("docker", "Dockerfile"),
    "containerfile": ("docker", "Containerfile"),
    "makefile": ("makefile", "Makefile"),
    "gnumakefile": ("makefile", "Makefile"),
    ".gitignore": ("none", "Git Ignore"),
    ".gitattributes": ("none", "Git Attributes"),
    ".gitmodules": ("none", "Git Modules"),
    ".env": ("bash", "Environment Config"),
    ".bashrc": ("bash", "Bash Config"),
    ".zshrc": ("bash", "Zsh Config"),
    ".profile": ("bash", "Shell Profile"),
    "requirements.txt": ("python", "Python Requirements"),
    "pyproject.toml": ("toml", "TOML Configuration"),
    "cargo.toml": ("toml", "Cargo Configuration"),
    "cargo.lock": ("toml", "Cargo Lock"),
    "cmakelists.txt": ("cmake", "CMakeLists"),
    "gemfile": ("ruby", "Gemfile"),
    "rakefile": ("ruby", "Rakefile"),
    "vagrantfile": ("ruby", "Vagrantfile"),
    "procfile": ("yaml", "Procfile"),
    "caddyfile": ("none", "Caddyfile"),
    "license": ("none", "License Text"),
    "licence": ("none", "License Text"),
    "readme": ("markdown", "Readme Document"),
}


def is_text_content(data: bytes) -> bool:
    """
    Determines whether a byte sequence is likely text rather than binary.
    Checks for null bytes in the first 8 KB and attempts UTF-8 decoding.
    """
    if not data:
        return True
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        text_chars = bytearray(
            {7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F}
        )
        non_text = sum(b not in text_chars for b in sample)
        return (non_text / len(sample)) < 0.1


def guess_mime_type(filename: str, sample_bytes: bytes | None = None) -> str:
    """
    Accurately determines MIME content type for uploads.
    Properly handles modern code files (.lua, .rs, .toml, .sh, etc.)
    that standard mimetypes.guess_type() either misses or misidentifies.
    """
    clean_name = filename.lower().strip()
    ext = os.path.splitext(clean_name)[1]
    base_name = os.path.basename(clean_name)

    # 1. Special filenames
    if base_name in SPECIAL_FILENAMES:
        if base_name in ("cargo.toml", "pyproject.toml"):
            return "text/plain"
        return "text/plain"

    # 2. Known code extensions
    if ext == ".rs":
        return "text/plain"
    if ext in (".lua", ".toml", ".sh", ".bash", ".zsh", ".fish", ".env"):
        return "text/plain"
    if ext in (".json", ".jsonc"):
        return "application/json"
    if ext in (".yaml", ".yml"):
        return "application/yaml"
    if ext in (".xml",):
        return "application/xml"
    if ext in (".html", ".htm"):
        return "text/html"
    if ext in (".css", ".scss", ".sass", ".less"):
        return "text/css"
    if ext in (".js", ".mjs", ".cjs"):
        return "application/javascript"
    if ext in (".svg",):
        return "image/svg+xml"
    if ext in LANGUAGE_MAP:
        return "text/plain"

    # 3. Standard library guess
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        if mime == "application/rls-services+xml":
            return "text/plain"
        if mime == "application/x-sh":
            return "text/plain"
        return mime

    # 4. Content inspection if sample bytes provided
    if sample_bytes and is_text_content(sample_bytes):
        return "text/plain"

    return "application/octet-stream"


def detect_preview_type(
    filename: str,
    content_type: str = "",
    sample_bytes: bytes | None = None,
) -> tuple[str, str, str]:
    """
    Returns (preview_type, detected_language, detected_type_label).
    preview_type is one of: 'image', 'video', 'audio', 'pdf', 'text', 'generic'.
    """
    clean_name = filename.lower().strip()
    ext = os.path.splitext(clean_name)[1]
    base_name = os.path.basename(clean_name)
    ct = (content_type or "").lower().strip()

    # 1. Images
    if ct.startswith("image/") or ext in (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
    ):
        type_name = ext.replace(".", "").upper() if ext else "IMAGE"
        return ("image", "", f"{type_name} Image")

    # 2. Videos
    if ct.startswith("video/") or ext in (
        ".mp4",
        ".webm",
        ".ogg",
        ".mov",
        ".mkv",
    ):
        type_name = ext.replace(".", "").upper() if ext else "VIDEO"
        return ("video", "", f"{type_name} Video")

    # 3. Audio
    if ct.startswith("audio/") or ext in (
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".m4a",
        ".aac",
    ):
        type_name = ext.replace(".", "").upper() if ext else "AUDIO"
        return ("audio", "", f"{type_name} Audio")

    # 4. PDF
    if ct == "application/pdf" or ext == ".pdf":
        return ("pdf", "", "PDF Document")

    # 5. Code & Text by special filename
    if base_name in SPECIAL_FILENAMES:
        lang, label = SPECIAL_FILENAMES[base_name]
        return ("text", lang, label)

    # 6. Code & Text by extension
    if ext in LANGUAGE_MAP:
        lang, label = LANGUAGE_MAP[ext]
        return ("text", lang, label)

    # 7. Code & Text by MIME
    if ct.startswith("text/") or ct in (
        "application/json",
        "application/javascript",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/toml",
        "application/x-sh",
    ):
        return ("text", "none", "Text File")

    # 8. Check sample bytes
    if sample_bytes and is_text_content(sample_bytes):
        return ("text", "none", "Text File")

    # 9. Generic / Binary
    label = f"{ext.replace('.', '').upper()} File" if ext else "Binary File"
    return ("generic", "", label)


def is_text_file(
    filename: str,
    content_type: str = "",
    file_path: str | None = None,
    sample_bytes: bytes | None = None,
) -> bool:
    """
    Returns True if the file is a text/code file.
    """
    clean_name = filename.lower().strip()
    ext = os.path.splitext(clean_name)[1]

    # Explicit image/video/audio/pdf formats should not be treated as text files
    if ext in (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
        ".mp4",
        ".webm",
        ".ogg",
        ".mov",
        ".mkv",
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".aac",
        ".pdf",
    ):
        return False

    ct = (content_type or "").lower().strip()
    if ct.startswith(("image/", "video/", "audio/")) or ct == "application/pdf":
        return False

    if sample_bytes is None and file_path and os.path.isfile(file_path):
        try:
            with open(file_path, "rb") as f:
                sample_bytes = f.read(8192)
        except OSError:
            pass

    preview_type, _, _ = detect_preview_type(
        filename=filename,
        content_type=content_type,
        sample_bytes=sample_bytes,
    )
    if preview_type == "text":
        return True

    if ct.startswith("text/"):
        return True

    return bool(sample_bytes and is_text_content(sample_bytes))
