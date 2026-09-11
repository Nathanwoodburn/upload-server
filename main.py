import os

import dotenv
from gunicorn.app.base import BaseApplication

import server


class GunicornApp(BaseApplication):
    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        for key, value in self.options.items():
            if key in self.cfg.settings and value is not None:
                self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


if __name__ == "__main__":
    dotenv.load_dotenv()

    workers = os.getenv("WORKERS", "1")
    threads = os.getenv("THREADS", "2")
    workers = int(workers)
    threads = int(threads)

    options = {
        "bind": "0.0.0.0:5000",
        "workers": workers,
        "threads": threads,
    }

    gunicorn_app = GunicornApp(server.app, options)
    print(f"Starting server with {workers} workers and {threads} threads", flush=True)
    gunicorn_app.run()
