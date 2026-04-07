import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, List, Tuple
from urllib import request


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.atlas_core.dev import SERVICES, build_runtime_environment


def wait_for_health(url: str, timeout_seconds: int = 20) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - dev helper
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError("Service health check failed for {0}: {1}".format(url, last_error))


def terminate_processes(processes: List[Tuple[subprocess.Popen, Path]]) -> None:
    for process, _ in processes:
        if process.poll() is None:
            process.terminate()
    for process, _ in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    runtime_dir = ROOT_DIR / "runtime"
    logs_dir = runtime_dir / "logs"
    if "--reset-data" in sys.argv and runtime_dir.exists():
        shutil.rmtree(runtime_dir)

    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = build_runtime_environment(ROOT_DIR, runtime_dir)
    processes: List[Tuple[subprocess.Popen, Path]] = []
    log_files: List[IO[str]] = []

    try:
        for spec in SERVICES:
            log_path = logs_dir / "{0}.log".format(spec.name)
            log_file = open(log_path, "w", encoding="utf-8")
            log_files.append(log_file)
            process = subprocess.Popen(  # noqa: S603
                [sys.executable, spec.script_path],
                cwd=str(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            processes.append((process, log_path))

        for spec in SERVICES:
            wait_for_health(spec.health_url)

        print("Atlas Core is running.")
        print("Gateway: http://127.0.0.1:7000")
        print("Logs: {0}".format(logs_dir))
        print("Use Ctrl+C to stop all services.")

        def handle_interrupt(signum, frame):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, handle_interrupt)
        signal.signal(signal.SIGTERM, handle_interrupt)

        while True:
            time.sleep(1)
            for process, log_path in processes:
                if process.poll() is not None:
                    raise RuntimeError(
                        "A service exited unexpectedly. Inspect the log: {0}".format(log_path)
                    )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return_code = 1
    else:
        return_code = 0
    finally:
        terminate_processes(processes)
        for log_file in log_files:
            log_file.close()

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
