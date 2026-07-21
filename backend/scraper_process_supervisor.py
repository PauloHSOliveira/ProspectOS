"""ScraperProcessSupervisor — supervised lifecycle for the Google Maps scraper process.

Owns exactly one Popen at a time. Rejects duplicate starts. Manages:
  - Process group (POSIX start_new_session / Windows CREATE_NEW_PROCESS_GROUP)
  - Graceful → forced termination with configurable deadlines
  - stdout/stderr readers with capped memory
  - State machine with closed transitions
  - Crash detection, timeout vs cancel distinction
"""

import json
import logging
import os
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


# ── Constants ──────────────────────────────────────────────────────────────

GRACEFUL_TERMINATION_TIMEOUT = 10.0
FORCED_TERMINATION_TIMEOUT = 5.0
READER_JOIN_TIMEOUT = 5.0
MAX_LOG_TAIL_LINES = 50
MAX_LINE_LENGTH = 2000
MAX_CAPTURED_BYTES = 100_000
STREAM_POLL_INTERVAL = 0.1


# ── Enums ──────────────────────────────────────────────────────────────────

class ScraperState(Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    TIMED_OUT = "TIMED_OUT"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    KILLED = "KILLED"


_SCRAPER_VALID_TRANSITIONS: dict[ScraperState, set[ScraperState]] = {
    ScraperState.IDLE: {ScraperState.STARTING},
    ScraperState.STARTING: {ScraperState.RUNNING, ScraperState.CANCELLING,
                            ScraperState.STOPPING, ScraperState.FAILED,
                            ScraperState.KILLED},
    ScraperState.RUNNING: {ScraperState.CANCELLING, ScraperState.TIMED_OUT,
                           ScraperState.STOPPING, ScraperState.COMPLETED,
                           ScraperState.FAILED, ScraperState.KILLED},
    ScraperState.CANCELLING: {ScraperState.STOPPING, ScraperState.STOPPED,
                              ScraperState.FAILED, ScraperState.KILLED},
    ScraperState.TIMED_OUT: {ScraperState.STOPPING, ScraperState.FAILED,
                             ScraperState.KILLED},
    ScraperState.STOPPING: {ScraperState.STOPPED, ScraperState.FAILED,
                            ScraperState.KILLED},
    ScraperState.STOPPED: {ScraperState.IDLE},
    ScraperState.COMPLETED: {ScraperState.IDLE},
    ScraperState.FAILED: {ScraperState.IDLE},
    ScraperState.KILLED: {ScraperState.IDLE},
}


class TerminationReason(Enum):
    NORMAL = "NORMAL"
    USER_CANCELLED = "USER_CANCELLED"
    APP_QUIT = "APP_QUIT"
    STARTUP_TIMEOUT = "STARTUP_TIMEOUT"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    BACKEND_CRASH = "BACKEND_CRASH"
    SCRAPER_ERROR = "SCRAPER_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    PARENT_EXIT = "PARENT_EXIT"
    FORCED = "FORCED"
    UNKNOWN = "UNKNOWN"


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class ScraperDiagnostics:
    state: str = "IDLE"
    pid: int | None = None
    process_group_id: int | None = None
    ppid: int | None = None
    started_at: str | None = None
    ready_at: str | None = None
    stop_requested_at: str | None = None
    exited_at: str | None = None
    return_code: int | None = None
    termination_reason: str | None = None
    graceful_termination_attempted: bool = False
    forced_termination_attempted: bool = False
    stdout_line_count: int = 0
    stderr_line_count: int = 0
    last_output: str | None = None


@dataclass
class ScraperResult:
    return_code: int | None
    state: ScraperState
    termination_reason: TerminationReason
    duration_seconds: float
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _Sentinel:
    pass


# ── Supervisor ─────────────────────────────────────────────────────────────

class ScraperProcessSupervisor:
    """Supervises exactly one scraper subprocess lifecycle.

    Usage:

        supervisor = ScraperProcessSupervisor()
        result = supervisor.run(
            executable=...,
            args=[...],
            cwd=...,
            env=...,
            timeout=900,
            cancel_event=cancel_ev,
            progress_callback=fn,
        )
        # or

        supervisor.start(...)
        # ... do other work ...
        supervisor.cancel()
        result = supervisor.wait()
    """

    def __init__(self):
        self._state = ScraperState.IDLE
        self._process: subprocess.Popen | None = None
        self._sentinel = _Sentinel()
        self._queue: queue.Queue = queue.Queue()
        self._reader_threads: list[threading.Thread] = []
        self._pid: int | None = None
        self._pgid: int | None = None
        self._ppid: int | None = None

        self._started_at: str | None = None
        self._ready_at: str | None = None
        self._stop_requested_at: str | None = None
        self._exited_at: str | None = None
        self._return_code: int | None = None
        self._termination_reason: TerminationReason = TerminationReason.UNKNOWN
        self._graceful_termination_attempted: bool = False
        self._forced_termination_attempted: bool = False

        self._stdout_tail: list[str] = []
        self._stderr_tail: list[str] = []
        self._errors: list[str] = []
        self._stdout_line_count: int = 0
        self._stderr_line_count: int = 0
        self._last_output: str | None = None
        self._captured_bytes: int = 0

        self._result: ScraperResult | None = None
        self._result_event = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────

    def run(
        self,
        executable: Path,
        args: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[["ParsedScraperLine"], None] | None = None,
    ) -> ScraperResult:
        """Run the scraper synchronously with full supervision.

        Blocks until the process completes, is cancelled, or times out.
        Returns a ScraperResult with final state and termination reason.
        """
        self.start(executable=executable, args=args, cwd=cwd, env=env)

        deadline = (time.monotonic() + timeout) if timeout else None

        process_exit_handled = False

        while True:
            if cancel_event and cancel_event.is_set():
                self.cancel()
                break

            if deadline is not None and time.monotonic() >= deadline:
                self._mark_timed_out()
                self._initiate_shutdown()
                break

            if self._process and self._process.poll() is not None and not process_exit_handled:
                self._on_process_exit()
                process_exit_handled = True

            try:
                item = self._queue.get(timeout=STREAM_POLL_INTERVAL)
            except queue.Empty:
                if self._sentinels_remaining <= 0:
                    break
                if process_exit_handled:
                    break
                continue

            if item is self._sentinel:
                self._on_reader_done()
                if self._sentinels_remaining <= 0:
                    break
                continue

            self._process_line(item, progress_callback)

        self._finish_shutdown()
        return self._result

    def start(
        self,
        executable: Path,
        args: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        """Start the scraper process asynchronously.

        Raises RuntimeError if already running.
        """
        if self._state not in (ScraperState.IDLE, ScraperState.STOPPED,
                                ScraperState.COMPLETED, ScraperState.FAILED,
                                ScraperState.KILLED):
            raise RuntimeError(
                f"ScraperProcessSupervisor: cannot start from state {self._state.value}"
            )

        self._reset_state()
        self._set_state(ScraperState.STARTING)
        self._started_at = _now_iso()

        self._log("starting process", {"executable": str(executable)})

        creationflags = 0
        start_new_session = False
        if IS_WINDOWS:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            start_new_session = True

        cmd = [str(executable), *args]

        self._process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )

        self._pid = self._process.pid
        self._ppid = os.getpid()

        if not IS_WINDOWS:
            try:
                self._pgid = os.getpgid(self._pid)
            except OSError:
                self._pgid = None
        else:
            self._pgid = self._pid

        self._log("process started", {"pid": self._pid, "pgid": self._pgid})

        self._start_readers()
        self._set_state(ScraperState.RUNNING)

    def wait(self) -> ScraperResult:
        """Wait for the scraper to finish and return the result.

        Blocks until the process exits, then cleans up.
        """
        if self._result is not None:
            return self._result

        proc = self._process

        if proc is not None:
            try:
                proc.wait(timeout=GRACEFUL_TERMINATION_TIMEOUT + FORCED_TERMINATION_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.kill()
                try:
                    proc.wait(timeout=FORCED_TERMINATION_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass

            self._return_code = proc.poll()
            self._exited_at = _now_iso()

            if self._termination_reason == TerminationReason.UNKNOWN:
                if self._return_code == 0:
                    self._termination_reason = TerminationReason.NORMAL
                else:
                    self._termination_reason = TerminationReason.SCRAPER_ERROR

            if self._state not in (ScraperState.COMPLETED, ScraperState.FAILED,
                                    ScraperState.KILLED, ScraperState.STOPPED):
                if self._return_code == 0:
                    self._set_state(ScraperState.COMPLETED)
                else:
                    self._set_state(ScraperState.FAILED)
        else:
            if self._termination_reason == TerminationReason.UNKNOWN:
                self._termination_reason = TerminationReason.UNKNOWN

        self._cleanup_pipes()
        self._join_readers()

        if self._result is None:
            self._result = self._build_result()
            self._result_event.set()

        return self._result

    def cancel(self) -> None:
        """Request cooperative cancellation of the scraper."""
        if self._state not in (ScraperState.STARTING, ScraperState.RUNNING):
            return
        self._set_state(ScraperState.CANCELLING)
        self._termination_reason = TerminationReason.USER_CANCELLED
        self._stop_requested_at = _now_iso()
        self._log("cancel requested")
        self.terminate()

    def terminate(self) -> None:
        """Send graceful termination signal."""
        self._graceful_termination_attempted = True
        proc = self._process
        if not proc or proc.poll() is not None:
            return

        self._log("sending graceful termination")

        if IS_WINDOWS:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                proc.terminate()
        else:
            if self._pgid:
                try:
                    os.killpg(self._pgid, signal.SIGTERM)
                except ProcessLookupError:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            else:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def kill(self) -> None:
        """Force-kill the process tree."""
        self._forced_termination_attempted = True
        proc = self._process
        if not proc or proc.poll() is not None:
            return

        self._log("sending forced termination")

        if IS_WINDOWS:
            self._windows_taskkill_fallback()
        else:
            if self._pgid:
                try:
                    os.killpg(self._pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
            try:
                proc.kill()
            except Exception:
                pass

    def is_running(self) -> bool:
        return self._state in (ScraperState.STARTING, ScraperState.RUNNING,
                                ScraperState.CANCELLING, ScraperState.TIMED_OUT,
                                ScraperState.STOPPING)

    def get_state(self) -> ScraperState:
        return self._state

    def get_diagnostics(self) -> ScraperDiagnostics:
        return ScraperDiagnostics(
            state=self._state.value,
            pid=self._pid,
            process_group_id=self._pgid,
            ppid=self._ppid,
            started_at=self._started_at,
            ready_at=self._ready_at,
            stop_requested_at=self._stop_requested_at,
            exited_at=self._exited_at,
            return_code=self._return_code,
            termination_reason=self._termination_reason.value,
            graceful_termination_attempted=self._graceful_termination_attempted,
            forced_termination_attempted=self._forced_termination_attempted,
            stdout_line_count=self._stdout_line_count,
            stderr_line_count=self._stderr_line_count,
            last_output=self._last_output,
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _set_state(self, new_state: ScraperState) -> None:
        old = self._state
        allowed = _SCRAPER_VALID_TRANSITIONS.get(old, set())
        if new_state not in allowed:
            logger.warning(
                "invalid scraper state transition: %s -> %s",
                old.value, new_state.value,
            )
            return
        self._state = new_state
        self._log("state transition", {"from": old.value, "to": new_state.value})

    def _log(self, message: str, extra: dict | None = None) -> None:
        pid_str = f"[pid={self._pid}]" if self._pid else ""
        state_str = f"[{self._state.value}]"
        parts = [f"[scraper-supervisor]{state_str}{pid_str}", message]
        if extra:
            parts.append(json.dumps(extra))
        logger.info(" ".join(parts))

    def _reset_state(self) -> None:
        self._process = None
        self._pid = None
        self._pgid = None
        self._ppid = None
        self._started_at = None
        self._ready_at = None
        self._stop_requested_at = None
        self._exited_at = None
        self._return_code = None
        self._termination_reason = TerminationReason.UNKNOWN
        self._graceful_termination_attempted = False
        self._forced_termination_attempted = False
        self._stdout_tail = []
        self._stderr_tail = []
        self._errors = []
        self._stdout_line_count = 0
        self._stderr_line_count = 0
        self._last_output = None
        self._captured_bytes = 0
        self._result = None
        self._result_event.clear()
        self._reader_threads = []
        self._queue = queue.Queue()

    def _start_readers(self) -> None:
        proc = self._process
        if not proc:
            return

        pipes_open = 0
        if proc.stdout is not None:
            pipes_open += 1
            t = threading.Thread(
                target=_stream_reader,
                args=(proc.stdout, "stdout", self._queue, self._sentinel),
                daemon=True,
            )
            t.start()
            self._reader_threads.append(t)

        if proc.stderr is not None:
            pipes_open += 1
            t = threading.Thread(
                target=_stream_reader,
                args=(proc.stderr, "stderr", self._queue, self._sentinel),
                daemon=True,
            )
            t.start()
            self._reader_threads.append(t)

        self._sentinels_remaining = pipes_open

    def _process_line(self, item, progress_callback) -> None:
        stream = item.stream
        text = str(item.text)

        if len(text) > MAX_LINE_LENGTH:
            text = text[:MAX_LINE_LENGTH] + "...[truncated]"

        self._last_output = text
        self._captured_bytes += len(text)

        tail = self._stderr_tail if stream == "stderr" else self._stdout_tail
        tail.append(text)
        if len(tail) > MAX_LOG_TAIL_LINES:
            tail.pop(0)

        if stream == "stdout":
            self._stdout_line_count += 1
        else:
            self._stderr_line_count += 1

        if self._captured_bytes > MAX_CAPTURED_BYTES:
            return

        parsed = _parse_scraper_line(stream, text)
        if parsed and parsed.category == "error":
            self._errors.append(parsed.message or text.strip())

        if progress_callback and parsed:
            progress_callback(parsed)

    def _on_process_exit(self) -> None:
        proc = self._process
        if not proc:
            return

        self._return_code = proc.poll()
        self._exited_at = _now_iso()

        if self._state == ScraperState.CANCELLING:
            self._termination_reason = TerminationReason.USER_CANCELLED
        elif self._state == ScraperState.TIMED_OUT:
            self._termination_reason = TerminationReason.EXECUTION_TIMEOUT
        elif self._return_code == 0:
            self._termination_reason = TerminationReason.NORMAL
        elif self._forced_termination_attempted:
            self._termination_reason = TerminationReason.FORCED
        elif self._graceful_termination_attempted:
            self._termination_reason = TerminationReason.USER_CANCELLED
        else:
            self._termination_reason = TerminationReason.SCRAPER_ERROR

        self._log("process exited", {
            "rc": self._return_code,
            "reason": self._termination_reason.value,
        })

        if self._state not in (ScraperState.STOPPING, ScraperState.CANCELLING,
                                ScraperState.STOPPED, ScraperState.COMPLETED,
                                ScraperState.FAILED, ScraperState.KILLED):
            if self._return_code == 0:
                self._set_state(ScraperState.COMPLETED)
            else:
                self._set_state(ScraperState.FAILED)

    def _on_reader_done(self) -> None:
        self._sentinels_remaining -= 1
        if self._sentinels_remaining <= 0:
            self._all_readers_done()

    def _all_readers_done(self) -> None:
        pass

    def _mark_timed_out(self) -> None:
        self._set_state(ScraperState.TIMED_OUT)
        self._termination_reason = TerminationReason.EXECUTION_TIMEOUT
        self._stop_requested_at = _now_iso()
        self._log("timeout expired")

    def _initiate_shutdown(self) -> None:
        if self._state in (ScraperState.STOPPING, ScraperState.STOPPED,
                            ScraperState.FAILED, ScraperState.KILLED):
            return

        self._set_state(ScraperState.STOPPING)
        self.terminate()

        deadline = time.monotonic() + GRACEFUL_TERMINATION_TIMEOUT
        proc = self._process

        while time.monotonic() < deadline:
            if proc is None or proc.poll() is not None:
                return
            time.sleep(0.05)

        self.kill()

    def _finish_shutdown(self) -> None:
        proc = self._process
        if proc is not None:
            if proc.poll() is None:
                deadline = time.monotonic() + FORCED_TERMINATION_TIMEOUT
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                if proc.poll() is None:
                    self.kill()
                    try:
                        proc.wait(timeout=FORCED_TERMINATION_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        pass

            if self._return_code is None:
                self._return_code = proc.poll()
            if self._exited_at is None:
                self._exited_at = _now_iso()

            if self._termination_reason == TerminationReason.UNKNOWN:
                if self._return_code == 0:
                    self._termination_reason = TerminationReason.NORMAL
                elif self._forced_termination_attempted:
                    self._termination_reason = TerminationReason.FORCED
                else:
                    self._termination_reason = TerminationReason.SCRAPER_ERROR

        self._cleanup_pipes()
        self._join_readers()

        if self._state not in (ScraperState.COMPLETED, ScraperState.FAILED,
                                ScraperState.KILLED):
            if self._forced_termination_attempted:
                self._set_state(ScraperState.KILLED)
            else:
                self._set_state(ScraperState.STOPPED)

        if self._result is None:
            self._result = self._build_result()
            self._result_event.set()
        self._log("shutdown complete")

    def _cleanup_pipes(self) -> None:
        proc = self._process
        if not proc:
            return
        for pipe_name in ("stdout", "stderr"):
            pipe = getattr(proc, pipe_name, None)
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

    def _join_readers(self) -> None:
        for t in self._reader_threads:
            t.join(timeout=READER_JOIN_TIMEOUT)
        self._reader_threads.clear()

    def _windows_taskkill_fallback(self) -> None:
        if not self._pid:
            return
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self._pid)],
                timeout=FORCED_TERMINATION_TIMEOUT,
                capture_output=True,
            )
        except Exception as exc:
            self._log("taskkill failed", {"error": str(exc)})

    def _build_result(self) -> ScraperResult:
        proc = self._process
        rc = proc.poll() if proc else self._return_code
        duration = 0.0
        if self._started_at:
            started = datetime.fromisoformat(self._started_at)
            ended = datetime.fromisoformat(self._exited_at or _now_iso())
            duration = (ended - started).total_seconds()

        return ScraperResult(
            return_code=rc,
            state=self._state,
            termination_reason=self._termination_reason,
            duration_seconds=duration,
            stdout_tail=list(self._stdout_tail),
            stderr_tail=list(self._stderr_tail),
            errors=list(self._errors),
        )


# ── Module-level helpers ───────────────────────────────────────────────────

@dataclass
class _ParsedLine:
    stream: str
    text: str
    category: str
    message: str = ""
    level: str = ""
    job_id: str = ""
    places_found: int = 0
    is_job_finished: bool = False


def _stream_reader(pipe, stream_name: str, q: queue.Queue, sentinel: _Sentinel) -> None:
    try:
        for raw_line in iter(pipe.readline, ""):
            q.put(_ParsedLine(stream=stream_name, text=raw_line, category="raw"))
    except ValueError:
        pass
    except OSError:
        pass
    finally:
        q.put(sentinel)
        try:
            pipe.close()
        except OSError:
            pass


def _parse_scraper_line(stream: str, text: str) -> _ParsedLine | None:
    text = text.rstrip("\n\r")
    if not text:
        return None

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return _ParsedLine(stream=stream, text=text, category="unstructured")

    if not isinstance(obj, dict):
        return _ParsedLine(stream=stream, text=text, category="unstructured")

    message = obj.get("message", "")
    level = obj.get("level", "")
    job_id = obj.get("jobid", "")

    if level == "error":
        return _ParsedLine(
            stream=stream, text=text, category="error",
            message=message, level=level, job_id=job_id,
        )

    if message == "job finished":
        return _ParsedLine(
            stream=stream, text=text, category="lifecycle",
            message=message, level=level, job_id=job_id,
            is_job_finished=True,
        )

    if message.endswith("places found"):
        parts = message.split(" ", 1)
        count = int(parts[0]) if parts[0].isdigit() else 0
        return _ParsedLine(
            stream=stream, text=text, category="progress",
            message=message, level=level, job_id=job_id,
            places_found=count,
        )

    if level in ("info", "debug", "warn"):
        return _ParsedLine(
            stream=stream, text=text, category="diagnostic",
            message=message, level=level, job_id=job_id,
        )

    return _ParsedLine(stream=stream, text=text, category="unstructured")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
