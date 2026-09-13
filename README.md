# Upload Server

A lightweight, high-performance file upload service designed for both CLI (`curl`, shell alias) and web browser workflows.

## Features

- **CLI-First**: Upload directly with `curl` or using an installable shell alias (`upload`).
- **One-Command Setup**: Install the shell function directly by serving it at `/bashrc`:
  ```bash
  curl -sL https://your-server/bashrc >> ~/.bashrc && source ~/.bashrc
  ```
- **Custom Expiry**: Specify expiration times (`30m`, `2h`, `1d`, `7d`, `30d`, `1y`) or default to **7 days**. Expired files are automatically cleaned up.
- **Delete URL**: Every upload returns a dedicated delete URL (`/delete/<token>`) allowing instantaneous one-shot deletion via CLI or confirmation in the browser.
- **Modify Link**: Customize the link slug (vanity URL), update the expiration date, or replace the file content via `/modify/<token>` or CLI.
- **List All Your Links**: View active links uploaded from your machine or browser:
  - **CLI**: Machine token persistence or header `X-User-Token` / `X-Machine-Id` lists your CLI uploads (`upload --list` or `curl -H "X-User-Token: ..." .../list`).
  - **Browser**: Cookie-based dashboard on the homepage and `/list` with instant token syncing between terminal and browser.

---

## Quick Start

### 1. Requirements
- Python 3.13+
- [uv](https://github.com/astral-sh/uv)

### 2. Run the Development Server
```bash
uv sync
uv run python3 server.py
```

### 3. Run in Production (Gunicorn)
```bash
uv run python3 main.py
```

---

## Usage Guide

### A. Shell Alias (Recommended)

1. Add the alias to your shell:
   ```bash
   curl -sL http://localhost:5000/bashrc >> ~/.bashrc && source ~/.bashrc
   ```
2. Upload a file (default 7-day expiry):
   ```bash
   upload photo.png
   ```
3. Upload with custom expiry and custom slug:
   ```bash
   upload report.pdf 24h quarterly-report
   ```
4. Upload piped stdin:
   ```bash
   cat /var/log/syslog | upload syslog.txt 1d
   ```
5. List active files uploaded by your machine:
   ```bash
   upload --list
   ```
6. Modify link slug or expiry:
   ```bash
   upload --modify <modify_token_or_url> new-slug 14d
   ```
7. Delete a file:
   ```bash
   upload --delete <delete_token_or_url>
   ```

---

### B. Upload with `curl`

**Basic upload (default 7 days):**
```bash
curl -F "file=@document.pdf" http://localhost:5000/
```

**Upload with custom expiry:**
```bash
curl -F "file=@document.pdf" -F "expires=3d" http://localhost:5000/
```

**Upload with custom link slug:**
```bash
curl -F "file=@document.pdf" -F "slug=my-docs" http://localhost:5000/
```

**Get raw download URL only:**
```bash
curl -s -F "file=@document.pdf" "http://localhost:5000/?raw=1"
```

**Upload with JSON output:**
```bash
curl -s -F "file=@document.pdf" -H "Accept: application/json" http://localhost:5000/
```

---

### C. Link Management & Deletion

- **Download:** `http://localhost:5000/u/<slug>` (or direct `http://localhost:5000/<slug>`)
- **Delete:** `curl -X POST http://localhost:5000/delete/<delete_token>` (or open in browser for confirmation)
- **Modify:** `curl -X POST http://localhost:5000/modify/<manage_token> -F "slug=new-slug" -F "expires=14d"`

---

## Testing & Quality

Run the test suite:
```bash
uv run pytest
```

Check linting and formatting:
```bash
uv run ruff check
uv run ruff format --check
```
