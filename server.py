import os
from datetime import UTC, datetime

import dotenv
import requests
from flask import (
    Flask,
    jsonify,
    make_response,
    render_template,
    request,
    send_file,
    send_from_directory,
)

dotenv.load_dotenv()

app = Flask(__name__)


def find(name, path):
    for root, dirs, files in os.walk(path):
        if name in files:
            return os.path.join(root, name)


# Assets routes
@app.route("/assets/<path:path>")
def send_assets(path):
    if path.endswith(".json"):
        return send_from_directory(
            "templates/assets", path, mimetype="application/json"
        )

    if os.path.isfile("templates/assets/" + path):
        return send_from_directory("templates/assets", path)

    # Try looking in one of the directories
    filename: str = path.split("/")[-1]
    if filename.endswith((".png", ".jpg", ".jpeg", ".svg")):
        if os.path.isfile("templates/assets/img/" + filename):
            return send_from_directory("templates/assets/img", filename)
        if os.path.isfile("templates/assets/img/favicon/" + filename):
            return send_from_directory("templates/assets/img/favicon", filename)

    return render_template("404.html"), 404


# region Special routes
@app.route("/favicon.png")
def faviconPNG():
    return send_from_directory("templates/assets/img", "favicon.png")


@app.route("/.well-known/<path:path>")
def wellknown(path):
    # Try to proxy to https://nathan.woodburn.au/.well-known/
    req = requests.get(f"https://nathan.woodburn.au/.well-known/{path}")
    return make_response(
        req.content, 200, {"Content-Type": req.headers["Content-Type"]}
    )


# endregion


# region Main routes
@app.route("/")
def index():
    # Print the IP address of the requester
    print(f"Request from IP: {request.remote_addr}")
    # And the headers
    print(f"Request headers: {request.headers}")
    # Get current time in the format "dd MMM YYYY hh:mm AM/PM"
    current_datetime = datetime.now(UTC).strftime("%d %b %Y %I:%M %p")
    return render_template("index.html", datetime=current_datetime)


@app.route("/<path:path>")
def catch_all(path: str):
    if os.path.isfile("templates/" + path):
        return render_template(path)

    # Try with .html
    if os.path.isfile("templates/" + path + ".html"):
        return render_template(path + ".html")

    if os.path.isfile("templates/" + path.strip("/") + ".html"):
        return render_template(path.strip("/") + ".html")

    # Try to find a file matching
    if path.count("/") < 1:
        # Try to find a file matching
        filename = find(path, "templates")
        if filename:
            return send_file(filename)

    return render_template("404.html"), 404


# endregion


# region API routes

api_requests = 0


@app.route("/api/v1/data", methods=["GET"])
def api_data():
    """
    Example API endpoint that returns some data.
    You can modify this to return whatever data you need.
    """

    global api_requests
    api_requests += 1

    data = {
        "header": "Sample API Response",
        "content": f"Hello, this is a sample API response! You have called this endpoint {api_requests} times.",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return jsonify(data)


# endregion


# region Error Catching
# 404 catch all
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# endregion
if __name__ == "__main__":
    app.run(debug=True, port=5000, host="127.0.0.1")
