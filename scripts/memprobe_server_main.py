"""MemProbe evaluation server launcher and owned-process lifecycle helpers."""

import argparse
import contextlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _production_home() -> Path:
    """Require an explicit production path; never infer it from evaluation config."""
    home = os.environ.get("REMEMBRANCE_PROD_HOME", "").strip()
    if not home:
        raise SystemExit("REMEMBRANCE_PROD_HOME 未设置，拒跑评测")
    return Path(home).expanduser().resolve()


def _assert_isolated_home(home: Path) -> Path:
    """Reject canonical path overlap with declared or default production homes."""
    production = _production_home()
    protected = [production, Path.home() / "AppData" / "Local" / "remembrance-data"]
    if local_appdata := os.environ.get("LOCALAPPDATA"):
        protected.append(Path(local_appdata) / "remembrance-data")
    evaluation = home.expanduser().resolve()
    ev = str(evaluation).casefold()
    for path in protected:
        prod = str(path.resolve()).casefold()
        try:
            common = os.path.commonpath((ev, prod))
        except ValueError:
            continue
        if common in (ev, prod):
            raise SystemExit("双闸失败：评测目录与受保护生产目录重叠，拒跑")
    return evaluation


def _required_setting(name: str, dotenv: dict[str, str]) -> str:
    """Use only configured credentials; empty values fail closed."""
    value = (os.environ.get(name) or dotenv.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} 未配置，拒跑评测")
    return value


def _stop_owned_process(proc, timeout: float = 5.0) -> None:
    """Stop only this launcher and descendants, including Windows venv wrappers."""
    if proc is None:
        return
    import psutil

    children = list(getattr(proc, "_evaluation_children", ()))
    if proc.poll() is None:
        with contextlib.suppress(psutil.NoSuchProcess):
            children.extend(psutil.Process(proc.pid).children(recursive=True))
    children = list({(child.pid, child.create_time()): child for child in children}.values())
    for child in reversed(children):
        with contextlib.suppress(psutil.NoSuchProcess):
            child.terminate()
    _, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            child.kill()
    _, alive = psutil.wait_procs(alive, timeout=timeout)
    if alive:
        raise RuntimeError("evaluation interpreter cleanup timed out")
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    else:
        proc.wait()


def _await_child_ready(proc, ready_file: Path, nonce: str, timeout: float = 120.0) -> dict:
    """Accept readiness only from a live descendant carrying this launch nonce."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            proc.wait()
            raise RuntimeError("evaluation child exited before readiness")
        try:
            record = json.loads(ready_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if (
            isinstance(record, dict)
            and record.get("nonce") == nonce
            and type(record.get("pid")) is int
            and isinstance(record.get("port"), int)
            and 0 < record["port"] < 65536
        ):
            import psutil

            try:
                server = psutil.Process(record["pid"])
                if server.pid != proc.pid and not any(
                    parent.pid == proc.pid for parent in server.parents()
                ):
                    time.sleep(0.1)
                    continue
                proc._evaluation_children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                continue
            if proc.poll() is None:
                return record
            proc.wait()
            raise RuntimeError("evaluation child exited after readiness")
        time.sleep(0.1)
    raise RuntimeError("evaluation child readiness timed out")


def _launch_owned_server(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout,
    stderr,
    timeout: float = 120.0,
    expected_port: int | None = None,
):
    """Launch and verify a nonce-bearing server before returning its process handle."""
    import shutil

    ready_dir = Path(tempfile.mkdtemp(prefix="memprobe-ready-"))
    ready_file = ready_dir / "ready.json"
    nonce = secrets.token_urlsafe(24)
    child_env = dict(env)
    child_env.update(MEMPROBE_READY_FILE=str(ready_file), MEMPROBE_READY_NONCE=nonce)
    proc = None
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=stdout, stderr=stderr)
        record = _await_child_ready(proc, ready_file, nonce, timeout)
        if expected_port is not None and record["port"] != expected_port:
            raise RuntimeError("evaluation child bound an unexpected port")
        if proc.poll() is not None:
            proc.wait()
            raise RuntimeError("evaluation child exited after readiness")
        return proc
    except BaseException:
        _stop_owned_process(proc)
        raise
    finally:
        shutil.rmtree(ready_dir, ignore_errors=True)


def _write_readiness(server) -> None:
    """Atomically publish the bound port after Uvicorn completed startup."""
    target = os.environ.get("MEMPROBE_READY_FILE")
    nonce = os.environ.get("MEMPROBE_READY_NONCE")
    if not target or not nonce:
        return
    listeners = [sock for srv in getattr(server, "servers", []) for sock in (srv.sockets or [])]
    if not listeners:
        raise RuntimeError("Uvicorn reports started without an active listener")
    ready_file = Path(target)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = ready_file.with_name(f"{ready_file.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(
        json.dumps({"nonce": nonce, "pid": os.getpid(), "port": listeners[0].getsockname()[1]}),
        encoding="utf-8",
    )
    os.replace(temporary, ready_file)


def _assert_double_gate() -> None:
    home = os.environ.get("LANTAI_HOME", "").strip()
    if not home:
        raise SystemExit("LANTAI_HOME 未设置，拒启")
    _assert_isolated_home(Path(home))
    print("[双闸通过] 评测目录已与受保护生产目录隔离", flush=True)


def _noop_telemetry() -> None:
    import lantai.llm.client as _client

    _client.record_llm_outcome = lambda *a, **k: None
    print(
        "[遥测] 评测服务器进程内 record_llm_outcome → no-op（30s/外呼死锁规避，见文件头）",
        flush=True,
    )


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    _assert_double_gate()
    _noop_telemetry()
    import uvicorn

    class ReadinessServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started and not self.should_exit:
                _write_readiness(self)

    config = uvicorn.Config(
        "lantai.api.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8793")),
        log_level="warning",
    )
    ReadinessServer(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
