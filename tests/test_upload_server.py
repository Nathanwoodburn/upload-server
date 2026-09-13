import io
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

import database
import server
from utils import parse_expiry


@pytest.fixture
def client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test_uploads.db")
    upload_dir = os.path.join(temp_dir, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    monkeypatch.setattr(database, "DB_PATH", db_file)
    monkeypatch.setitem(server.app.config, "UPLOAD_FOLDER", upload_dir)
    server.app.config["TESTING"] = True

    database.init_db()

    with server.app.test_client() as test_client:
        yield test_client


def test_bashrc_endpoint(client):
    """Test that /bashrc serves valid bash script with expected functions and aliases."""
    response = client.get("/bashrc")
    assert response.status_code == 200
    assert "text/plain" in response.content_type
    content = response.data.decode("utf-8")
    assert "upload()" in content
    assert "alias upload-list='upload --list'" in content
    assert "alias upload-delete='upload --delete'" in content
    assert "alias upload-modify='upload --modify'" in content
    assert "X-User-Token" in content


def test_upload_default_expiry(client):
    """Test file upload defaults to 7 days expiry."""
    data = {
        "file": (io.BytesIO(b"Hello world test content"), "hello.txt"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data")
    assert response.status_code in (200, 201)
    text = response.data.decode("utf-8")
    assert "Download URL:" in text
    assert "Delete URL:" in text
    assert "Modify URL:" in text
    assert "hello.txt" in text
    assert "in 6d 23h" in text or "in 7d" in text


def test_upload_json_response(client):
    """Test upload with Accept: application/json returns structured metadata."""
    data = {
        "file": (io.BytesIO(b"JSON upload test"), "data.json"),
        "expires": "2d",
    }
    headers = {"Accept": "application/json"}
    response = client.post(
        "/upload", data=data, headers=headers, content_type="multipart/form-data"
    )
    assert response.status_code == 201
    json_data = response.get_json()
    assert json_data["status"] == "success"
    assert json_data["filename"] == "data.json"
    assert "download_url" in json_data
    assert "delete_url" in json_data
    assert "modify_url" in json_data
    assert "user_token" in json_data


def test_upload_raw_single_line(client):
    """Test upload with ?raw=1 returns only the download URL."""
    data = {
        "file": (io.BytesIO(b"Raw URL test"), "short.txt"),
        "slug": "direct-raw",
    }
    res = client.post("/upload?raw=1", data=data)
    assert res.status_code == 200
    assert res.data.decode("utf-8").strip().endswith("/u/direct-raw")


def test_upload_custom_slug(client):
    """Test uploading with custom slug and downloading the file."""
    data = {
        "file": (io.BytesIO(b"Custom slug content"), "custom.txt"),
        "slug": "my-custom-doc",
    }
    headers = {"Accept": "application/json"}
    response = client.post("/upload", data=data, headers=headers)
    assert response.status_code == 201
    json_data = response.get_json()
    assert json_data["slug"] == "my-custom-doc"

    # Download by slug
    dl_resp = client.get("/u/my-custom-doc")
    assert dl_resp.status_code == 200
    assert dl_resp.data == b"Custom slug content"

    # Also works via direct /<slug>
    direct_resp = client.get("/my-custom-doc")
    assert direct_resp.status_code == 200
    assert direct_resp.data == b"Custom slug content"


def test_custom_slug_collision(client):
    """Test duplicate slug rejection."""
    data1 = {"file": (io.BytesIO(b"file 1"), "file1.txt"), "slug": "same-slug"}
    res1 = client.post("/upload", data=data1)
    assert res1.status_code in (200, 201)

    data2 = {"file": (io.BytesIO(b"file 2"), "file2.txt"), "slug": "same-slug"}
    res2 = client.post("/upload", data=data2)
    assert res2.status_code == 409


def test_reserved_and_invalid_slugs(client):
    """Test rejection of reserved slugs and invalid characters."""
    for bad_slug in (
        "bashrc",
        "delete",
        "modify",
        "u",
        "assets",
        "with spaces",
        "invalid/slash",
    ):
        data = {"file": (io.BytesIO(b"test"), "bad.txt"), "slug": bad_slug}
        res = client.post("/upload", data=data)
        assert res.status_code == 400


def test_delete_url(client):
    """Test deleting uploaded file with delete URL."""
    data = {"file": (io.BytesIO(b"Delete me soon"), "temp.txt")}
    res = client.post("/upload", data=data, headers={"Accept": "application/json"})
    json_data = res.get_json()
    delete_url = json_data["delete_url"]
    slug = json_data["slug"]

    # File exists
    assert client.get(f"/u/{slug}").status_code == 200

    # Extract delete path
    delete_path = "/" + delete_url.split("/", 3)[-1]

    # CLI curl delete
    del_res = client.post(delete_path, headers={"User-Agent": "curl/8.5.0"})
    assert del_res.status_code == 200
    assert "successfully deleted" in del_res.data.decode("utf-8")

    # File should no longer exist
    assert client.get(f"/u/{slug}").status_code == 404


def test_browser_delete_confirmation(client):
    """Test browser GET to delete URL renders confirmation, POST deletes."""
    data = {"file": (io.BytesIO(b"Browser delete test"), "bdel.txt")}
    res = client.post("/upload", data=data, headers={"Accept": "application/json"})
    delete_path = "/" + res.get_json()["delete_url"].split("/", 3)[-1]

    # Browser GET request
    get_res = client.get(
        delete_path, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"}
    )
    assert get_res.status_code == 200
    assert b"Confirm Deletion" in get_res.data

    # Browser POST confirm
    post_del = client.post(
        delete_path, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"}
    )
    assert post_del.status_code == 302


def test_modify_link_slug_and_expiry(client):
    """Test modifying link slug and expiration via manage token."""
    data = {"file": (io.BytesIO(b"Editable document"), "original.txt")}
    res = client.post("/upload", data=data, headers={"Accept": "application/json"})
    json_data = res.get_json()
    modify_url = json_data["modify_url"]
    old_slug = json_data["slug"]
    modify_path = "/" + modify_url.split("/", 3)[-1]

    # Modify slug to 'new-renamed-link' and expiry to '30d'
    mod_res = client.post(
        modify_path,
        data={"slug": "new-renamed-link", "expires": "30d"},
        headers={"Accept": "application/json"},
    )
    assert mod_res.status_code == 200
    mod_json = mod_res.get_json()
    assert mod_json["status"] == "success"
    assert mod_json["file"]["slug"] == "new-renamed-link"

    # Old slug should be 404
    assert client.get(f"/u/{old_slug}").status_code == 404

    # New slug should serve original content
    new_dl = client.get("/u/new-renamed-link")
    assert new_dl.status_code == 200
    assert new_dl.data == b"Editable document"


def test_modify_replace_file(client):
    """Test replacing the file content under the same link."""
    data = {"file": (io.BytesIO(b"Version 1.0"), "v1.txt"), "slug": "versioned-file"}
    res = client.post("/upload", data=data, headers={"Accept": "application/json"})
    modify_path = "/" + res.get_json()["modify_url"].split("/", 3)[-1]

    # Replace file
    replace_res = client.post(
        modify_path,
        data={"file": (io.BytesIO(b"Version 2.0 updated!"), "v2.txt")},
        headers={"Accept": "application/json"},
    )
    assert replace_res.status_code == 200

    # Verify updated content under same slug
    dl_res = client.get("/u/versioned-file")
    assert dl_res.status_code == 200
    assert dl_res.data == b"Version 2.0 updated!"


def test_list_files_by_token(client):
    """Test listing files by machine UUID / user token header and cookie."""
    token1 = "test_user_alpha"
    token2 = "test_user_beta"

    # Upload 2 files for token1
    client.post(
        "/upload",
        data={"file": (io.BytesIO(b"file 1"), "file1.txt")},
        headers={"X-User-Token": token1},
    )
    client.post(
        "/upload",
        data={"file": (io.BytesIO(b"file 2"), "file2.txt")},
        headers={"X-User-Token": token1},
    )

    # Upload 1 file for token2
    client.post(
        "/upload",
        data={"file": (io.BytesIO(b"file 3"), "file3.txt")},
        headers={"X-User-Token": token2},
    )

    # List token1 files via CLI header
    res1 = client.get(
        "/list", headers={"X-User-Token": token1, "Accept": "application/json"}
    )
    assert res1.status_code == 200
    json1 = res1.get_json()
    assert json1["count"] == 2
    assert {f["original_filename"] for f in json1["files"]} == {
        "file1.txt",
        "file2.txt",
    }

    # List token2 files via cookie
    client.set_cookie("user_token", token2)
    res2 = client.get("/list", headers={"Accept": "application/json"})
    assert res2.status_code == 200
    json2 = res2.get_json()
    assert json2["count"] == 1
    assert json2["files"][0]["original_filename"] == "file3.txt"


def test_raw_binary_upload(client):
    """Test raw binary upload with X-Filename header."""
    res = client.post(
        "/upload",
        data=b"Binary file payload here",
        headers={"X-Filename": "binary.bin", "Accept": "application/json"},
    )
    assert res.status_code == 201
    json_data = res.get_json()
    assert json_data["filename"] == "binary.bin"
    assert json_data["size"] == 24


def test_switch_token(client):
    """Test browser token switching to sync with CLI."""
    res = client.post("/api/token/switch", data={"token": "synced_machine_123"})
    assert res.status_code == 302
    assert "user_token=synced_machine_123" in res.headers.get("Set-Cookie", "")


def test_expiry_parsing():
    """Test parsing various expiry string units."""
    now = datetime.now(UTC)
    # Default 7 days
    exp_default = parse_expiry(None)
    assert 6 <= (exp_default - now).days <= 7

    # 1 hour
    exp_1h = parse_expiry("1h")
    assert 3500 <= (exp_1h - now).total_seconds() <= 3700

    # 14 days
    exp_14d = parse_expiry("14d")
    assert 13 <= (exp_14d - now).days <= 14

    # 30 days
    exp_30d = parse_expiry("30d")
    assert 29 <= (exp_30d - now).days <= 30


def test_expiration_cleanup(client):
    """Test that expired files return 410 and are cleaned up."""
    past_date = datetime.now(UTC) - timedelta(hours=1)
    file_path = os.path.join(server.app.config["UPLOAD_FOLDER"], "expired.txt")
    with open(file_path, "wb") as f:
        f.write(b"Should be deleted")

    database.save_file_record(
        slug="expired-slug",
        original_filename="expired.txt",
        stored_filename="expired.txt",
        file_size=17,
        content_type="text/plain",
        user_token="user_exp",
        manage_token="mod_exp",
        delete_token="del_exp",
        created_at=past_date - timedelta(days=7),
        expires_at=past_date,
    )

    # Accessing expired file returns 410
    res = client.get("/u/expired-slug", headers={"User-Agent": "curl/8.5.0"})
    assert res.status_code == 410

    # Physical file should be removed from disk
    assert not os.path.exists(file_path)


def test_delete_by_slug_with_ownership(client):
    """Test that a user can delete their file directly using the slug."""
    owner_token = "user_owner_42"
    res = client.post(
        "/upload",
        data={
            "file": (io.BytesIO(b"Delete by slug test"), "slugdel.txt"),
            "slug": "slug-to-delete",
        },
        headers={"X-User-Token": owner_token, "Accept": "application/json"},
    )
    assert res.status_code == 201

    # Attempt to delete with matching owner token
    del_res = client.post(
        "/delete/slug-to-delete",
        headers={"X-User-Token": owner_token, "User-Agent": "curl/8.5.0"},
    )
    assert del_res.status_code == 200
    assert "successfully deleted" in del_res.data.decode("utf-8")
    assert client.get("/u/slug-to-delete").status_code == 404


def test_delete_with_full_url(client):
    """Test deleting by passing a full URL like http://.../u/slug or http://.../delete/token."""
    owner_token = "user_owner_99"
    res = client.post(
        "/upload",
        data={
            "file": (io.BytesIO(b"Full URL test"), "fullurl.txt"),
            "slug": "full-url-slug",
        },
        headers={"X-User-Token": owner_token, "Accept": "application/json"},
    )
    assert res.status_code == 201

    # Pass full download URL into delete endpoint
    del_res = client.post(
        "/delete/http://127.0.0.1:5000/u/full-url-slug",
        headers={"X-User-Token": owner_token, "User-Agent": "curl/8.5.0"},
    )
    assert del_res.status_code == 200
    assert "successfully deleted" in del_res.data.decode("utf-8")
    assert client.get("/u/full-url-slug").status_code == 404


def test_get_upload_redirects_to_index(client):
    """Test that GET /upload?message=... cleanly redirects to /?message=..."""
    res = client.get("/upload?message=Device+token+updated!")
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/?message=Device+token+updated!")


def test_service_worker_endpoint(client):
    """Test that /sw.js returns 204 without 404 errors."""
    res = client.get("/sw.js")
    assert res.status_code == 204


def test_browser_code_file_preview(client):
    """Test that browser GET /u/<slug> renders rich preview with code contents."""
    lua_code = b'local keybinds = { open = "<leader>e" }\nreturn keybinds\n'
    res = client.post(
        "/upload",
        data={"file": (io.BytesIO(lua_code), "keybinds.lua"), "slug": "my-keybinds"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 201

    # Browser GET request with Accept: text/html
    preview_res = client.get(
        "/u/my-keybinds",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0",
        },
    )
    assert preview_res.status_code == 200
    assert "text/html" in preview_res.content_type
    html = preview_res.data.decode("utf-8")
    assert "keybinds.lua" in html
    assert "Lua Script" in html
    assert "local keybinds" in html
    assert "language-lua" in html
    assert "/u/my-keybinds?download=1" in html
    assert "/u/my-keybinds?raw=1" in html


def test_browser_image_preview(client):
    """Test that browser GET /u/<slug> renders image preview with <img> tag."""
    dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    res = client.post(
        "/upload",
        data={"file": (io.BytesIO(dummy_png), "avatar.png"), "slug": "my-avatar"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 201

    # Browser GET
    preview_res = client.get(
        "/u/my-avatar",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    assert preview_res.status_code == 200
    html = preview_res.data.decode("utf-8")
    assert "<img" in html
    assert "/u/my-avatar?raw=1" in html


def test_browser_raw_and_download_params(client):
    """Test ?raw=1 and ?download=1 bypass preview even in browser."""
    file_bytes = b"Raw bytes test content"
    client.post(
        "/upload",
        data={"file": (io.BytesIO(file_bytes), "sample.txt"), "slug": "sample-doc"},
    )

    # ?raw=1 returns raw file
    raw_res = client.get(
        "/u/sample-doc?raw=1",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    assert raw_res.status_code == 200
    assert raw_res.data == file_bytes

    # ?download=1 returns attachment
    dl_res = client.get(
        "/u/sample-doc?download=1",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    assert dl_res.status_code == 200
    assert dl_res.data == file_bytes
    assert "attachment" in dl_res.headers.get("Content-Disposition", "")
    assert "sample.txt" in dl_res.headers.get("Content-Disposition", "")


def test_view_raw_text_files_inline_in_browser(client):
    """
    Test that viewing raw code/text files in the browser (?raw=1) serves them
    with Content-Type: text/plain; charset=utf-8 and no attachment header,
    so browsers display them inline instead of downloading.
    """
    test_files = [
        ("keybinds.lua", b'local keybinds = { open = "<leader>e" }\n', "lua-slug"),
        ("install.sh", b'#!/bin/bash\necho "Installing..."\n', "sh-slug"),
        ("config.toml", b"[server]\nport = 5000\n", "toml-slug"),
        ("main.rs", b'fn main() { println!("Hello"); }\n', "rs-slug"),
        ("script.py", b'print("Hello Python")\n', "py-slug"),
        ("Dockerfile", b"FROM alpine:latest\nCMD echo hello\n", "docker-slug"),
        (".env", b"SECRET_KEY=supersecret\n", "env-slug"),
    ]

    for filename, content, slug in test_files:
        up_res = client.post(
            "/upload",
            data={"file": (io.BytesIO(content), filename), "slug": slug},
            headers={"Accept": "application/json"},
        )
        assert up_res.status_code == 201

        # View raw in browser
        raw_res = client.get(
            f"/u/{slug}?raw=1",
            headers={
                "Accept": "text/html,application/xhtml+xml,text/plain",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            },
        )
        assert raw_res.status_code == 200
        assert raw_res.data == content
        # Must be text/plain so browsers render it directly in the tab
        assert "text/plain" in raw_res.content_type
        # Must NOT be an attachment
        cd = raw_res.headers.get("Content-Disposition", "")
        assert "attachment" not in cd
        # Must have nosniff
        assert raw_res.headers.get("X-Content-Type-Options") == "nosniff"

        # Explicit download (?download=1) MUST still be an attachment
        dl_res = client.get(
            f"/u/{slug}?download=1",
            headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
        )
        assert dl_res.status_code == 200
        assert "attachment" in dl_res.headers.get("Content-Disposition", "")
        assert filename in dl_res.headers.get("Content-Disposition", "")


def test_legacy_octet_stream_text_file_serves_inline_raw(client):
    """
    Test that existing files in SQLite previously stored with application/octet-stream
    are dynamically recognized and served inline as text/plain when viewed raw.
    """
    code_content = b'print("Legacy lua script")\n'
    file_path = os.path.join(server.app.config["UPLOAD_FOLDER"], "legacy.lua")
    with open(file_path, "wb") as f:
        f.write(code_content)

    now = datetime.now(UTC)
    database.save_file_record(
        slug="legacy-lua",
        original_filename="script.lua",
        stored_filename="legacy.lua",
        file_size=len(code_content),
        content_type="application/octet-stream",  # old fallback
        user_token="user_legacy",
        manage_token="mod_legacy",
        delete_token="del_legacy",
        created_at=now,
        expires_at=now + timedelta(days=7),
    )

    # Browser raw request
    res = client.get(
        "/u/legacy-lua?raw=1",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    assert res.status_code == 200
    assert res.data == code_content
    assert "text/plain" in res.content_type
    assert "attachment" not in res.headers.get("Content-Disposition", "")
