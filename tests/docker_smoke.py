"""Optional Docker lifecycle check; build the Compose image first.

Uses an isolated Compose project, port, and temporary data/backup directories.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    with tempfile.TemporaryDirectory(prefix="hub-docker-") as directory:
        root = Path(directory)
        data, backups = root / "data", root / "backups"
        data.mkdir()
        backups.mkdir()
        port = int(os.environ.get("HUB_TEST_PORT", "18765"))
        config = root / "compose.yaml"
        config.write_text("services:\n  hub:\n    image: github_local_hub-hub\n    ports:\n"
                          f"      - '127.0.0.1:{port}:8765'\n    volumes:\n"
                          f"      - {json.dumps(str(data) + ':/data')}\n"
                          f"      - {json.dumps(str(backups) + ':/backups')}\n"
                          "    environment:\n      GITHUB_TOKEN: ''\n    cap_drop: [ALL]\n    cap_add: [DAC_OVERRIDE]\n"
                          "    security_opt: ['no-new-privileges:true']\n")
        command = ["docker", "--host=unix:///var/run/docker.sock", "compose", "-p", "hub-mvp-lifecycle", "-f", str(config)]
        environment = dict(os.environ)
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            environment.pop(key, None)

        def compose(*args):
            subprocess.run([*command, *args], env=environment, check=True, timeout=45,
                           stdout=None if args[0] == "logs" else subprocess.DEVNULL)

        base = f"http://127.0.0.1:{port}"

        def api(path, body=None):
            if os.environ.get("HUB_TEST_EXEC_HTTP") == "1":
                # Managed Docker-in-Docker may not forward inner ports to the
                # outer executor. Exercise the exact same HTTP API in-container.
                payload = None if body is None else json.dumps(body).encode()
                code = ("import json,urllib.request; "
                        f"r=urllib.request.Request({'http://127.0.0.1:8765' + path!r},data={payload!r},"
                        "headers={'Content-Type':'application/json','X-Hub-Request':'1','Origin':'http://127.0.0.1:8765'}); "
                        "print(urllib.request.urlopen(r,timeout=3).read().decode())")
                result = subprocess.run([*command, "exec", "-T", "hub", "python", "-c", code],
                                        env=environment, check=True, timeout=10, capture_output=True, text=True)
                return json.loads(result.stdout)
            headers = {"Content-Type": "application/json", "X-Hub-Request": "1", "Origin": base}
            request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.load(response)

        def start():
            compose("up", "-d")
            deadline = time.monotonic() + 20
            while True:
                try:
                    if api("/api/health")["ok"]:
                        return
                except (OSError, urllib.error.URLError, subprocess.CalledProcessError):
                    if time.monotonic() >= deadline:
                        compose("logs", "--tail", "30", "hub")
                        raise
                time.sleep(0.2)

        try:
            start()
            api("/api/repositories", {"repo": "demo/sample"})
            api("/api/filters", {"repo": "demo/sample", "kind": "pr", "state": "merged", "labels": ["custom:test"]})
            assert (data / "hub.sqlite3").is_file()
            assert (backups / "latest.sqlite3").is_file()
            compose("down")
            start()
            result = api("/api/status")
            assert result["repositories"][0]["name"] == "demo/sample"
            assert result["filters"]["labels"] == ["custom:test"]
            compose("down")
            (data / "hub.sqlite3").unlink()
            compose("run", "--rm", "--no-deps", "hub", "python", "-m", "hub.restore", "--backup", "/backups/latest.sqlite3", "--data", "/data")
            start()
            result = api("/api/status")
            assert result["repositories"][0]["name"] == "demo/sample"
            assert result["filters"]["state"] == "merged"
            assert result["backup_error"] is None
            print("Docker lifecycle passed: boot, host persistence, container recreation, offline restore, restored settings.")
        finally:
            compose("down")


if __name__ == "__main__":
    main()
