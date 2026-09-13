import os
from datetime import UTC, datetime
from uuid import uuid4

import dotenv
import requests
from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

import database
from utils import (
    detect_preview_type,
    format_relative_time,
    format_size,
    generate_slug,
    generate_token,
    get_base_url,
    get_user_token,
    guess_mime_type,
    is_text_file,
    is_valid_slug,
    parse_expiry,
)

dotenv.load_dotenv()

app = Flask(__name__)

# Base directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(BASE_DIR, "data", "uploads"))
MAX_CONTENT_LENGTH = int(
    os.getenv("MAX_CONTENT_LENGTH", str(500 * 1024 * 1024))
)  # 500 MB default

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
database.init_db()


def is_cli_client() -> bool:
    """Detect if request is from a CLI tool like curl, wget, or python script."""
    user_agent = request.headers.get("User-Agent", "").lower()
    accept = request.headers.get("Accept", "").lower()

    if any(
        agent in user_agent
        for agent in (
            "curl",
            "wget",
            "httpie",
            "python",
            "aria2",
            "requests",
            "werkzeug",
        )
    ):
        return True
    return "text/html" not in accept


def wants_json() -> bool:
    """Detect if caller requested JSON response."""
    return (
        request.headers.get("Accept") == "application/json"
        or request.args.get("format") == "json"
        or request.form.get("format") == "json"
    )


def build_file_response_data(file_row: dict, base_url: str) -> dict:
    """Build standardized dictionary representation of an upload."""
    slug = file_row["slug"]
    manage_token = file_row["manage_token"]
    delete_token = file_row["delete_token"]
    expires_at_dt = datetime.fromisoformat(file_row["expires_at"])

    return {
        "id": file_row["id"],
        "slug": slug,
        "original_filename": file_row["original_filename"],
        "size": file_row["file_size"],
        "human_size": format_size(file_row["file_size"]),
        "content_type": file_row["content_type"],
        "created_at": file_row["created_at"],
        "expires_at": file_row["expires_at"],
        "relative_expiry": format_relative_time(expires_at_dt),
        "downloads_count": file_row.get("downloads_count", 0),
        "download_url": f"{base_url}/u/{slug}",
        "delete_url": f"{base_url}/delete/{delete_token}",
        "modify_url": f"{base_url}/modify/{manage_token}",
        "manage_token": manage_token,
        "delete_token": delete_token,
        "user_token": file_row["user_token"],
    }


def find(name, path):
    for root, _, files in os.walk(path):
        if name in files:
            return os.path.join(root, name)
    return None


# region Assets & Static routes
@app.route("/assets/<path:path>")
def send_assets(path):
    if path.endswith(".json"):
        return send_from_directory(
            "templates/assets", path, mimetype="application/json"
        )

    if os.path.isfile("templates/assets/" + path):
        return send_from_directory("templates/assets", path)

    filename: str = path.split("/")[-1]
    if filename.endswith((".png", ".jpg", ".jpeg", ".svg")):
        if os.path.isfile("templates/assets/img/" + filename):
            return send_from_directory("templates/assets/img", filename)
        if os.path.isfile("templates/assets/img/favicon/" + filename):
            return send_from_directory("templates/assets/img/favicon", filename)

    return render_template("404.html"), 404


@app.route("/favicon.png")
@app.route("/favicon.ico")
def faviconPNG():
    return send_from_directory("templates/assets/img", "favicon.png")


@app.route("/sw.js")
def service_worker():
    """Handle browser service worker polling cleanly with 204 No Content."""
    return ("", 204)


@app.route("/.well-known/<path:path>")
def wellknown(path):
    try:
        req = requests.get(f"https://nathan.woodburn.au/.well-known/{path}", timeout=5)
        return make_response(
            req.content,
            req.status_code,
            {"Content-Type": req.headers.get("Content-Type", "text/plain")},
        )
    except requests.RequestException:
        return ("Not Found", 404)


# endregion


# region Bashrc Endpoint
@app.route("/bashrc", methods=["GET"])
def serve_bashrc():
    """Serves an easy-to-install shell client/alias script."""
    base_url = get_base_url(request)
    script = f"""#!/usr/bin/env bash
# ==============================================================================
# CLI Upload Client for {base_url}
# ==============================================================================
# Installation:
#   curl -sL {base_url}/bashrc >> ~/.bashrc && source ~/.bashrc
# Or temporary use in current terminal session:
#   source <(curl -sL {base_url}/bashrc)
# ==============================================================================

UPLOAD_SERVER_URL="{base_url}"
UPLOAD_CONFIG_DIR="${{XDG_CONFIG_HOME:-$HOME/.config}}/upload-server"
UPLOAD_TOKEN_FILE="$UPLOAD_CONFIG_DIR/token"

_get_upload_token() {{
    if [ -f "$UPLOAD_TOKEN_FILE" ]; then
        head -n 1 "$UPLOAD_TOKEN_FILE" | tr -d ' \\r\\n'
    elif [ -f /etc/machine-id ]; then
        head -n 1 /etc/machine-id | tr -d ' \\r\\n'
    elif [ -f /var/lib/dbus/machine-id ]; then
        head -n 1 /var/lib/dbus/machine-id | tr -d ' \\r\\n'
    else
        mkdir -p "$UPLOAD_CONFIG_DIR" 2>/dev/null
        local NEW_TOKEN="usr_$(od -An -tx1 -N16 /dev/urandom 2>/dev/null | tr -d ' \\n' || date +%s%N)"
        echo "$NEW_TOKEN" > "$UPLOAD_TOKEN_FILE" 2>/dev/null
        echo "$NEW_TOKEN"
    fi
}}

upload() {{
    local SERVER="${{UPLOAD_SERVER_URL:-"{base_url}"}}"
    local USER_TOKEN="$(_get_upload_token)"

    if [ "$#" -eq 0 ] && [ -t 0 ]; then
        echo "Usage: upload <file> [expiry] [slug]"
        echo "       upload --list                      # List your active uploads"
        echo "       upload --delete <slug|url|token>   # Delete an upload"
        echo "       upload --modify <slug|url|token> [new_slug] [new_expiry]"
        echo "       upload --token                     # Show user token"
        echo "       cat file.txt | upload [filename] [expiry]"
        echo ""
        echo "Examples:"
        echo "  upload report.pdf"
        echo "  upload image.png 24h"
        echo "  upload archive.tar.gz 30d my-backup"
        echo "  upload --list"
        echo "  upload --delete my-backup"
        return 1
    fi

    case "$1" in
        -h|--help)
            echo "CLI Upload Client for $SERVER"
            echo ""
            echo "Commands & Options:"
            echo "  upload <file> [expiry] [slug]         Upload a file (default expiry: 7 days)"
            echo "  upload -l, --list                     List your active links"
            echo "  upload -d, --delete <slug|url|token>  Delete a link"
            echo "  upload -m, --modify <slug|url|token>  Modify link slug or expiry"
            echo "  upload -t, --token                    Show user token (paste in web UI to sync)"
            echo "  cat file | upload <name> [expiry]     Upload piped stdin content"
            echo ""
            echo "Expiry units: 30m, 2h, 1d, 7d (default), 14d, 30d, 1y"
            return 0
            ;;
        -l|--list)
            curl -s -H "X-User-Token: $USER_TOKEN" "$SERVER/list"
            return $?
            ;;
        -t|--token)
            echo "User Token: $USER_TOKEN"
            echo "Paste this token into $SERVER to access your CLI uploads in the browser."
            return 0
            ;;
        -d|--delete)
            if [ -z "$2" ]; then
                echo "Error: Delete slug, URL, or token required. Example: upload --delete <slug|url|token>"
                return 1
            fi
            local TARGET="$2"
            if [[ "$TARGET" =~ /(delete|d|modify|m|u)/([^/?#]+) ]]; then
                TARGET="${{BASH_REMATCH[2]}}"
            elif [[ "$TARGET" =~ ^https?://[^/]+/([^/?#]+)$ ]]; then
                TARGET="${{BASH_REMATCH[1]}}"
            fi
            curl -s -X POST -H "X-User-Token: $USER_TOKEN" "$SERVER/delete/$TARGET"
            echo ""
            return $?
            ;;
        -m|--modify)
            if [ -z "$2" ]; then
                echo "Error: Modify slug, URL, or token required. Example: upload --modify <slug|url|token> [new_slug] [new_expiry]"
                return 1
            fi
            local TARGET="$2"
            if [[ "$TARGET" =~ /(delete|d|modify|m|u)/([^/?#]+) ]]; then
                TARGET="${{BASH_REMATCH[2]}}"
            elif [[ "$TARGET" =~ ^https?://[^/]+/([^/?#]+)$ ]]; then
                TARGET="${{BASH_REMATCH[1]}}"
            fi
            local NEW_SLUG="$3"
            local NEW_EXP="$4"
            local CURL_ARGS=("-s" "-X" "POST" "-H" "X-User-Token: $USER_TOKEN")
            if [ -n "$NEW_SLUG" ]; then
                CURL_ARGS+=("-F" "slug=$NEW_SLUG")
            fi
            if [ -n "$NEW_EXP" ]; then
                CURL_ARGS+=("-F" "expires=$NEW_EXP")
            fi
            curl "${{CURL_ARGS[@]}}" "$SERVER/modify/$TARGET"
            echo ""
            return $?
            ;;
    esac

    local FILE_PATH="$1"
    local EXPIRY="${{2:-7d}}"
    local SLUG="$3"

    if [ ! -t 0 ]; then
        local TMP_FILE
        TMP_FILE=$(mktemp)
        cat > "$TMP_FILE"
        local FILENAME="${{FILE_PATH:-stdin.txt}}"
        local CURL_CMD=("curl" "-#" "-F" "file=@$TMP_FILE;filename=$FILENAME" "-F" "expires=$EXPIRY" "-H" "X-User-Token: $USER_TOKEN")
        if [ -n "$SLUG" ]; then
            CURL_CMD+=("-F" "slug=$SLUG")
        fi
        local RES
        RES=$("${{CURL_CMD[@]}}" "$SERVER/upload")
        rm -f "$TMP_FILE"
        echo "$RES"
    else
        if [ ! -f "$FILE_PATH" ]; then
            echo "Error: File '$FILE_PATH' not found."
            return 1
        fi
        local CURL_CMD=("curl" "-#" "-F" "file=@$FILE_PATH" "-F" "expires=$EXPIRY" "-H" "X-User-Token: $USER_TOKEN")
        if [ -n "$SLUG" ]; then
            CURL_CMD+=("-F" "slug=$SLUG")
        fi
        local RES
        RES=$("${{CURL_CMD[@]}}" "$SERVER/upload")
        echo "$RES"
    fi
}}

alias upload-list='upload --list'
alias upload-delete='upload --delete'
alias upload-modify='upload --modify'
"""
    return Response(script, mimetype="text/plain; charset=utf-8")


# endregion


# region Upload & Index Routes
@app.route("/", methods=["GET", "POST"], endpoint="index")
@app.route("/upload", methods=["GET", "POST"], endpoint="upload")
def handle_index_or_upload():
    # If GET to /upload, cleanly redirect to index with any query parameters
    if request.method == "GET" and request.path == "/upload":
        return redirect(url_for("index", **request.args))

    base_url = get_base_url(request)
    database.cleanup_expired_files(app.config["UPLOAD_FOLDER"])
    user_token, is_new_user = get_user_token(request)

    # 1. GET requests to root
    if request.method == "GET":
        if is_cli_client():
            # Clean CLI guide
            help_text = (
                f"File Upload Server ({base_url})\n"
                f"--------------------------------------------------\n"
                f"Upload a file using curl:\n"
                f'  curl -F "file=@photo.png" {base_url}/\n'
                f'  curl -F "file=@photo.png" -F "expires=3d" {base_url}/\n'
                f'  curl -F "file=@photo.png" -F "slug=custom-name" {base_url}/\n\n'
                f"Install the convenient shell alias:\n"
                f"  curl -sL {base_url}/bashrc >> ~/.bashrc && source ~/.bashrc\n\n"
                f"List active uploads:\n"
                f'  curl -H "X-User-Token: {user_token}" {base_url}/list\n'
            )
            resp = Response(help_text, mimetype="text/plain; charset=utf-8")
            if is_new_user:
                resp.set_cookie(
                    "user_token", user_token, max_age=31536000, samesite="Lax"
                )
            return resp

        # Browser GET -> render web UI
        files_data = database.get_files_by_user(user_token)
        formatted_files = [build_file_response_data(f, base_url) for f in files_data]
        resp = make_response(
            render_template(
                "index.html",
                base_url=base_url,
                user_token=user_token,
                files=formatted_files,
                message=request.args.get("message"),
                error=request.args.get("error"),
            )
        )
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp

    # 2. POST requests -> File upload
    uploaded_file = None
    original_filename = None

    if request.files:
        for key in ("file", "data", "upload"):
            if key in request.files:
                uploaded_file = request.files[key]
                break
        if not uploaded_file:
            uploaded_file = next(iter(request.files.values()))
        original_filename = uploaded_file.filename

    # Fallback to raw binary body
    raw_data = None
    if not uploaded_file or not original_filename:
        raw_data = request.get_data()
        if not raw_data:
            err_msg = "Error: No file provided. Use curl -F 'file=@filename' <url> or upload a file."
            if wants_json():
                return jsonify({"error": err_msg}), 400
            return (err_msg + "\n", 400)
        header_filename = request.headers.get("X-Filename")
        original_filename = header_filename or "upload.bin"

    # Sanitize filename
    safe_name = secure_filename(original_filename)
    if not safe_name or safe_name == "-":
        safe_name = "upload.bin"

    # Handle expiry (form field, query, or header)
    expiry_input = (
        request.form.get("expires")
        or request.form.get("expiry")
        or request.form.get("expire")
        or request.args.get("expires")
        or request.args.get("expiry")
        or request.headers.get("X-Expires")
        or request.headers.get("X-Expiry")
    )
    expires_at = parse_expiry(expiry_input)
    now = datetime.now(UTC)

    # Handle custom slug
    requested_slug = (
        request.form.get("slug")
        or request.form.get("custom_slug")
        or request.args.get("slug")
        or request.headers.get("X-Slug")
    )

    if requested_slug:
        slug = requested_slug.strip()
        if not is_valid_slug(slug):
            err_msg = f"Error: Slug '{slug}' is invalid or reserved."
            if wants_json():
                return jsonify({"error": err_msg}), 400
            return (err_msg + "\n", 400)
        existing = database.get_file_by_slug(slug)
        if existing:
            # If expired, remove it to allow reuse
            if datetime.fromisoformat(existing["expires_at"]) <= now:
                database.delete_file_record(existing["id"])
            else:
                err_msg = f"Error: Slug '{slug}' is already in use."
                if wants_json():
                    return jsonify({"error": err_msg}), 409
                return (err_msg + "\n", 409)
    else:
        # Generate unique slug
        while True:
            slug = generate_slug(6)
            if not database.get_file_by_slug(slug) and is_valid_slug(slug):
                break

    # Tokens
    manage_token = generate_token("mod_")
    delete_token = generate_token("del_")

    # Save file to disk
    unique_prefix = uuid4().hex[:12]
    stored_filename = f"{unique_prefix}_{safe_name}"
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_filename)

    if uploaded_file and hasattr(uploaded_file, "save"):
        uploaded_file.save(file_path)
        file_size = os.path.getsize(file_path)
    elif raw_data is not None:
        with open(file_path, "wb") as f:
            f.write(raw_data)
        file_size = len(raw_data)
    else:
        return ("Error saving file\n", 500)

    # Detect MIME content type
    sample_chunk = b""
    try:
        with open(file_path, "rb") as f:
            sample_chunk = f.read(8192)
    except OSError:
        pass
    mime_type = guess_mime_type(original_filename, sample_chunk)

    # Save to SQLite
    database.save_file_record(
        slug=slug,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_size=file_size,
        content_type=mime_type,
        user_token=user_token,
        manage_token=manage_token,
        delete_token=delete_token,
        created_at=now,
        expires_at=expires_at,
    )

    download_url = f"{base_url}/u/{slug}"
    delete_url = f"{base_url}/delete/{delete_token}"
    modify_url = f"{base_url}/modify/{manage_token}"
    rel_exp = format_relative_time(expires_at)

    # Raw single-line output if requested
    if request.args.get("raw") == "1" or request.args.get("short") == "1":
        resp = Response(f"{download_url}\n", mimetype="text/plain; charset=utf-8")
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp

    # JSON response
    if wants_json():
        resp_data = {
            "status": "success",
            "filename": original_filename,
            "slug": slug,
            "size": file_size,
            "human_size": format_size(file_size),
            "download_url": download_url,
            "delete_url": delete_url,
            "modify_url": modify_url,
            "expires_at": expires_at.isoformat(),
            "expires_in": rel_exp,
            "user_token": user_token,
        }
        resp = jsonify(resp_data)
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp, 201

    # Browser redirect
    if not is_cli_client():
        resp = redirect(
            url_for(
                "index",
                message=f"Uploaded '{original_filename}' successfully!",
            )
        )
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp

    # Formatted CLI response
    cli_response = (
        f"File:         {original_filename} ({format_size(file_size)})\n"
        f"Download URL: {download_url}\n"
        f"Delete URL:   {delete_url}\n"
        f"Modify URL:   {modify_url}\n"
        f"Expires:      {expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')} (in {rel_exp})\n"
        f"User Token:   {user_token}\n"
    )
    resp = Response(cli_response, mimetype="text/plain; charset=utf-8")
    if is_new_user:
        resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
    return resp, 201


# endregion


# region File Access / Download Routes
@app.route("/u/<path:slug>", methods=["GET"])
def view_file_by_slug(slug: str):
    """Serves file preview or raw download by slug."""
    file_record = database.get_file_by_slug(slug)
    if not file_record:
        return render_template("404.html"), 404

    # Check expiration
    expires_at = datetime.fromisoformat(file_record["expires_at"])
    if expires_at <= datetime.now(UTC):
        stored = database.delete_file_record(file_record["id"])
        if stored:
            p = os.path.join(app.config["UPLOAD_FOLDER"], stored)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if is_cli_client():
            return ("Error: This file has expired and was removed.\n", 410)
        return render_template("404.html"), 410

    file_path = os.path.join(
        app.config["UPLOAD_FOLDER"], file_record["stored_filename"]
    )
    if not os.path.isfile(file_path):
        return render_template("404.html"), 404

    wants_raw = request.args.get("raw") == "1"
    wants_download = (
        request.args.get("download") == "1" or request.args.get("dl") == "1"
    )
    accept_html = "text/html" in request.headers.get("Accept", "")

    # If raw requested, download requested, or CLI client (curl, wget, etc.), serve raw file
    if wants_raw or wants_download or is_cli_client() or not accept_html:
        database.increment_download_count(file_record["id"])

        if wants_download:
            return send_file(
                file_path,
                mimetype=file_record["content_type"],
                as_attachment=True,
                download_name=file_record["original_filename"],
                conditional=True,
            )

        # Serving raw / inline (e.g. "View Raw" clicked, curl, or direct link)
        if is_text_file(
            file_record["original_filename"],
            file_record["content_type"],
            file_path=file_path,
        ):
            resp = send_file(
                file_path,
                mimetype="text/plain",
                as_attachment=False,
                download_name=None,
                conditional=True,
            )
            resp.headers.pop("Content-Disposition", None)
            resp.headers["X-Content-Type-Options"] = "nosniff"
            return resp

        # Non-text files (images, audio, video, pdf, or binary)
        preview_type, _, _ = detect_preview_type(
            file_record["original_filename"], file_record["content_type"]
        )
        if preview_type in ("image", "video", "audio", "pdf"):
            resp = send_file(
                file_path,
                mimetype=file_record["content_type"],
                as_attachment=False,
                download_name=None,
                conditional=True,
            )
            resp.headers.pop("Content-Disposition", None)
            return resp
        else:
            return send_file(
                file_path,
                mimetype=file_record["content_type"],
                as_attachment=False,
                download_name=file_record["original_filename"],
                conditional=True,
            )

    # Web browser request -> render rich interactive preview page
    base_url = get_base_url(request)
    user_token, _ = get_user_token(request)
    is_owner = file_record["user_token"] == user_token

    sample_chunk = b""
    try:
        with open(file_path, "rb") as f:
            sample_chunk = f.read(8192)
    except OSError:
        pass

    preview_type, detected_language, detected_type_label = detect_preview_type(
        file_record["original_filename"],
        file_record["content_type"],
        sample_bytes=sample_chunk,
    )

    text_content = ""
    display_text = ""
    is_truncated = False
    line_count = 0

    if preview_type == "text":
        max_preview_bytes = 1024 * 1024  # 1 MB
        if file_record["file_size"] > max_preview_bytes:
            is_truncated = True
        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read(max_preview_bytes)
            text_content = raw_bytes.decode("utf-8", errors="replace")
            lines = text_content.splitlines()
            line_count = len(lines)

            # Strip single trailing file-terminator newline for <pre><code> display so the
            # browser does not render an extra unnumbered blank line at the bottom
            display_text = text_content
            if display_text.endswith("\r\n"):
                display_text = display_text[:-2]
            elif display_text.endswith("\n"):
                display_text = display_text[:-1]
        except OSError:
            preview_type = "generic"

    return render_template(
        "preview.html",
        file=file_record,
        human_size=format_size(file_record["file_size"]),
        relative_expiry=format_relative_time(expires_at),
        preview_type=preview_type,
        detected_language=detected_language,
        detected_type_label=detected_type_label,
        text_content=text_content,
        display_text=display_text,
        line_count=line_count,
        is_truncated=is_truncated,
        download_url=f"{base_url}/u/{slug}?download=1",
        raw_url=f"{base_url}/u/{slug}?raw=1",
        modify_url=f"{base_url}/modify/{file_record['manage_token']}",
        delete_url=f"{base_url}/delete/{file_record['delete_token']}",
        is_owner=is_owner,
    )


# endregion


# region Delete Routes
@app.route("/delete/<path:token>", methods=["GET", "POST", "DELETE"])
@app.route("/d/<path:token>", methods=["GET", "POST", "DELETE"])
def delete_file_endpoint(token: str):
    """Deletes a file using its secret delete token, manage token, or slug (with ownership)."""
    clean_token = token.strip()
    if "://" in clean_token:
        parts = clean_token.split("://", 1)[1].split("/", 1)
        clean_token = parts[1] if len(parts) > 1 else parts[0]

    for prefix in ("delete/", "d/", "modify/", "m/", "u/"):
        if clean_token.startswith(prefix):
            clean_token = clean_token[len(prefix) :]
            break

    file_record = database.get_file_by_delete_token(clean_token)
    if not file_record:
        file_record = database.get_file_by_manage_token(clean_token)
    if not file_record:
        # Check if user passed slug
        candidate = database.get_file_by_slug(clean_token)
        if candidate:
            user_token, _ = get_user_token(request)
            if candidate["user_token"] == user_token or request.args.get("token") in (
                candidate["delete_token"],
                candidate["manage_token"],
            ):
                file_record = candidate
            else:
                err = "Error: Permission denied. Deleting by slug requires the owner's token or delete token."
                if wants_json():
                    return jsonify({"error": err}), 403
                if is_cli_client():
                    return (err + "\n", 403)
                return render_template("delete_confirm.html", file=None, error=err), 403

    if not file_record:
        if wants_json():
            return jsonify({"error": "File not found or already deleted"}), 404
        if is_cli_client():
            return ("Error: File not found or already deleted.\n", 404)
        return render_template("delete_confirm.html", file=None), 404

    filename = file_record["original_filename"]
    slug = file_record["slug"]

    # Browser GET without ?confirm=1 shows confirmation page
    if (
        request.method == "GET"
        and not is_cli_client()
        and request.args.get("confirm") != "1"
    ):
        return render_template(
            "delete_confirm.html",
            file=file_record,
            human_size=format_size(file_record["file_size"]),
        )

    # Perform deletion
    stored = database.delete_file_record(file_record["id"])
    if stored:
        p = os.path.join(app.config["UPLOAD_FOLDER"], stored)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    if wants_json():
        return jsonify(
            {"status": "success", "message": f"File '{filename}' successfully deleted."}
        )

    if is_cli_client():
        return f"✓ File '{filename}' (slug: {slug}) successfully deleted.\n"

    return redirect(
        url_for("index", message=f"File '{filename}' deleted successfully.")
    )


# endregion


# region Modify Routes
@app.route("/modify/<path:token>", methods=["GET", "POST", "PATCH"])
@app.route("/m/<path:token>", methods=["GET", "POST", "PATCH"])
def modify_file_endpoint(token: str):
    """Allows modifying link slug, expiry, or replacing file content."""
    base_url = get_base_url(request)
    clean_token = token.strip()
    if "://" in clean_token:
        parts = clean_token.split("://", 1)[1].split("/", 1)
        clean_token = parts[1] if len(parts) > 1 else parts[0]

    for prefix in ("modify/", "m/", "delete/", "d/", "u/"):
        if clean_token.startswith(prefix):
            clean_token = clean_token[len(prefix) :]
            break

    file_record = database.get_file_by_manage_token(clean_token)
    if not file_record:
        candidate = database.get_file_by_slug(clean_token)
        if candidate:
            user_token, _ = get_user_token(request)
            if candidate["user_token"] == user_token:
                file_record = candidate
            else:
                err = "Error: Permission denied. Modifying by slug requires the owner's token or manage token."
                if wants_json():
                    return jsonify({"error": err}), 403
                if is_cli_client():
                    return (err + "\n", 403)
                return render_template("404.html"), 403

    if not file_record:
        if wants_json():
            return jsonify({"error": "Invalid manage token or file expired."}), 404
        if is_cli_client():
            return ("Error: Invalid modify token or file not found.\n", 404)
        return render_template("404.html"), 404

    file_id = file_record["id"]
    now = datetime.now(UTC)

    # GET Request: Return modification details or form
    if request.method == "GET":
        expires_at = datetime.fromisoformat(file_record["expires_at"])
        if is_cli_client():
            info = (
                f"File:         {file_record['original_filename']} ({format_size(file_record['file_size'])})\n"
                f"Current Slug: {file_record['slug']}\n"
                f"Download URL: {base_url}/u/{file_record['slug']}\n"
                f"Expires At:   {file_record['expires_at']} (in {format_relative_time(expires_at)})\n"
                f"Downloads:    {file_record['downloads_count']}\n\n"
                f"To modify via curl:\n"
                f'  Change slug:   curl -X POST {base_url}/modify/{clean_token} -F "slug=new-slug"\n'
                f'  Change expiry: curl -X POST {base_url}/modify/{clean_token} -F "expires=14d"\n'
                f'  Replace file:  curl -X POST {base_url}/modify/{clean_token} -F "file=@newfile.txt"\n'
            )
            return Response(info, mimetype="text/plain; charset=utf-8")

        return render_template(
            "modify.html",
            file=file_record,
            human_size=format_size(file_record["file_size"]),
            relative_expiry=format_relative_time(expires_at),
            download_url=f"{base_url}/u/{file_record['slug']}",
            message=request.args.get("message"),
            error=request.args.get("error"),
        )

    # POST/PATCH Request: Perform updates
    new_slug = request.form.get("slug") or (
        request.is_json and request.json.get("slug")
    )
    new_expiry_str = request.form.get("expires") or (
        request.is_json and request.json.get("expires")
    )
    replacement_file = request.files.get("file")

    changes = []

    # 1. Update slug
    if new_slug and new_slug.strip() != file_record["slug"]:
        clean_new_slug = new_slug.strip()
        if not is_valid_slug(clean_new_slug):
            err = f"Error: Slug '{clean_new_slug}' is invalid or reserved."
            if wants_json():
                return jsonify({"error": err}), 400
            if is_cli_client():
                return (err + "\n", 400)
            return redirect(
                url_for("modify_file_endpoint", token=clean_token, error=err)
            )

        existing = database.get_file_by_slug(clean_new_slug)
        if existing and existing["id"] != file_id:
            if datetime.fromisoformat(existing["expires_at"]) <= now:
                database.delete_file_record(existing["id"])
            else:
                err = f"Error: Slug '{clean_new_slug}' is already in use."
                if wants_json():
                    return jsonify({"error": err}), 409
                if is_cli_client():
                    return (err + "\n", 409)
                return redirect(
                    url_for("modify_file_endpoint", token=clean_token, error=err)
                )

        success = database.update_file_slug(file_id, clean_new_slug)
        if not success:
            err = "Error: Could not update slug."
            if wants_json():
                return jsonify({"error": err}), 409
            return (err + "\n", 409)
        changes.append(f"slug updated to '{clean_new_slug}'")

    # 2. Update expiry
    if new_expiry_str and new_expiry_str.strip():
        new_expires_at = parse_expiry(new_expiry_str)
        database.update_file_expiry(file_id, new_expires_at)
        changes.append(
            f"expiry updated to {new_expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

    # 3. Replace file content if provided
    if replacement_file and replacement_file.filename:
        old_stored = file_record["stored_filename"]
        new_orig_name = replacement_file.filename
        new_safe_name = secure_filename(new_orig_name) or "upload.bin"
        new_stored = f"{uuid4().hex[:12]}_{new_safe_name}"
        new_path = os.path.join(app.config["UPLOAD_FOLDER"], new_stored)

        replacement_file.save(new_path)
        new_size = os.path.getsize(new_path)
        sample_chunk = b""
        try:
            with open(new_path, "rb") as f:
                sample_chunk = f.read(8192)
        except OSError:
            pass
        new_mime = guess_mime_type(new_orig_name, sample_chunk)

        database.update_file_content(
            file_id, new_orig_name, new_stored, new_size, new_mime
        )

        # Remove old physical file
        if old_stored:
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_stored)
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        changes.append(f"file content replaced with '{new_orig_name}'")

    # Fetch updated record
    updated_record = database.get_file_by_manage_token(file_record["manage_token"])
    updated_data = build_file_response_data(updated_record, base_url)

    if wants_json():
        return jsonify({"status": "success", "changes": changes, "file": updated_data})

    change_summary = ", ".join(changes) if changes else "No changes specified."
    if is_cli_client():
        return (
            f"✓ Link updated: {change_summary}\n"
            f"Download URL: {updated_data['download_url']}\n"
            f"Expires At:   {updated_data['expires_at']} (in {updated_data['relative_expiry']})\n"
        )

    return redirect(
        url_for(
            "modify_file_endpoint",
            token=file_record["manage_token"],
            message="Changes saved successfully!",
        )
    )


# endregion


# region List / User Files Routes
@app.route("/list", methods=["GET"])
@app.route("/mine", methods=["GET"])
def list_user_files():
    """Lists all active files associated with the requester's user token / machine id."""
    base_url = get_base_url(request)
    database.cleanup_expired_files(app.config["UPLOAD_FOLDER"])
    user_token, is_new_user = get_user_token(request)

    files_data = database.get_files_by_user(user_token)
    formatted = [build_file_response_data(f, base_url) for f in files_data]

    if wants_json():
        resp = jsonify(
            {"user_token": user_token, "count": len(formatted), "files": formatted}
        )
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp

    if is_cli_client():
        if not formatted:
            text = f"No active uploads found for user token: {user_token}\n"
        else:
            lines = [
                f"Active uploads for token: {user_token}",
                f"{'SLUG':<12} {'FILENAME':<24} {'SIZE':<10} {'EXPIRES':<10} {'DOWNLOAD URL'}",
                "-" * 80,
            ]
            for f in formatted:
                slug = f["slug"][:11]
                name = (
                    (f["original_filename"][:21] + "...")
                    if len(f["original_filename"]) > 24
                    else f["original_filename"]
                )
                size = f["human_size"]
                exp = f["relative_expiry"]
                url = f["download_url"]
                lines.append(f"{slug:<12} {name:<24} {size:<10} {exp:<10} {url}")
            lines.append("-" * 80)
            lines.append("Quick commands:")
            lines.append("  Delete a file: upload --delete <slug|url>")
            lines.append(
                "  Modify a file: upload --modify <slug|url> [new_slug] [new_expiry]"
            )
            text = "\n".join(lines) + "\n"

        resp = Response(text, mimetype="text/plain; charset=utf-8")
        if is_new_user:
            resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
        return resp

    resp = make_response(
        render_template(
            "list.html", files=formatted, user_token=user_token, base_url=base_url
        )
    )
    if is_new_user:
        resp.set_cookie("user_token", user_token, max_age=31536000, samesite="Lax")
    return resp


@app.route("/api/token/switch", methods=["POST"])
def switch_token():
    """Allows user to set/switch their browser cookie token to match their CLI token."""
    new_token = request.form.get("token") or (
        request.is_json and request.json.get("token")
    )
    if not new_token or not new_token.strip():
        return redirect(url_for("index", error="Token cannot be empty"))

    token = new_token.strip()
    if wants_json():
        resp = jsonify({"status": "success", "token": token})
    else:
        resp = redirect(url_for("index", message="Device token updated!"))
    resp.set_cookie("user_token", token, max_age=31536000, samesite="Lax")
    return resp


# endregion


# region Catch-all and Fallback
@app.route("/<path:path>", methods=["GET"])
def catch_all(path: str):
    """
    Catch-all route:
    1. Checks if path matches an active uploaded slug
    2. Checks if path matches a template or static asset
    3. Returns 404
    """
    # 1. Direct slug match
    file_record = database.get_file_by_slug(path)
    if file_record:
        return view_file_by_slug(path)

    # 2. Template / file match
    if os.path.isfile("templates/" + path):
        return render_template(path)

    if os.path.isfile("templates/" + path + ".html"):
        return render_template(path + ".html")

    if os.path.isfile("templates/" + path.strip("/") + ".html"):
        return render_template(path.strip("/") + ".html")

    if path.count("/") < 1:
        filename = find(path, "templates")
        if filename:
            return send_file(filename)

    return render_template("404.html"), 404


# endregion


# region Error Catching
@app.errorhandler(404)
def not_found(e):
    if is_cli_client() or wants_json():
        return ("404 Not Found\n", 404)
    return render_template("404.html"), 404


@app.errorhandler(413)
def file_too_large(e):
    err = f"Error: File exceeds maximum allowed size ({format_size(app.config['MAX_CONTENT_LENGTH'])}).\n"
    if wants_json():
        return jsonify({"error": err}), 413
    return (err, 413)


# endregion


if __name__ == "__main__":
    app.run(debug=True, port=5000, host="127.0.0.1")
