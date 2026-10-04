import os
from .db import Database
from .github import GitHub
from .server import make_server
from .service import Hub


def main():
    db = Database(os.environ.get("HUB_DATA", "data"), os.environ.get("HUB_BACKUPS", "backups"))
    hub = Hub(db, GitHub(os.environ.get("GITHUB_TOKEN", "")))
    server = make_server(hub, os.environ.get("HUB_HOST", "127.0.0.1"), int(os.environ.get("HUB_PORT", "8765")))
    print(f"GitHub Local Hub: http://localhost:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        db.close()


if __name__ == "__main__":
    main()
