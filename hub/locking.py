from pathlib import Path
import os


class DataLock:
    """Prevent a second app or restore process from using the same data directory."""
    def __init__(self, data_dir):
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.file = (path / "hub.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("This data directory is in use. Stop the app before restoring or starting another instance.") from None

    def close(self):
        self.file.close()
