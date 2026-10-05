import os
import argparse
from .db import Database
from .github import GitHub
from .server import make_server
from .service import Hub


def main():
    parser = argparse.ArgumentParser(description="Run the local GitHub hub HTTP server.")
    parser.add_argument("--host", default=os.environ.get("HUB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HUB_PORT", "8765")))
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    db = Database(os.environ.get("HUB_DATA", "data"), os.environ.get("HUB_BACKUPS", "backups"))
    hub = Hub(db, GitHub(os.environ.get("GITHUB_TOKEN", "")))
    server = make_server(hub, args.host, args.port)
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
