"""Development preview with storage separate from Docker's production mounts."""
import os
from .__main__ import main


if __name__ == "__main__":
    os.environ.setdefault("HUB_DATA", ".preview/data")
    os.environ.setdefault("HUB_BACKUPS", ".preview/backups")
    main()
