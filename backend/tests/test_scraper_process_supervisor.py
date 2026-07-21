"""Tests for scraper_process_supervisor.py — state machine, process lifecycle, shutdown."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper_process_supervisor import (
    ScraperProcessSupervisor,
    ScraperState,
    ScraperResult,
    TerminationReason,
    GRACEFUL_TERMINATION_TIMEOUT,
)


def _make_script(code: str) -> str:
    return f"import sys\n{code}"


def _python_code(code: str) -> list[str]:
    return ["-c", _make_script(code)]


class TestScraperStateTransitions:
    def test_starts_idle(self):
        sup = ScraperProcessSupervisor()
        assert sup.get_state() == ScraperState.IDLE

    def test_can_start_from_idle(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup.start(
            executable=Path(sys.executable),
            args=_python_code("print('hello')"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert sup.get_state() in (ScraperState.RUNNING, ScraperState.COMPLETED)
        sup._finish_shutdown()

    def test_rejects_duplicate_start(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup.start(
            executable=Path(sys.executable),
            args=_python_code("import time; time.sleep(5)"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        with pytest.raises(RuntimeError, match="cannot start"):
            sup.start(
                executable=Path(sys.executable),
                args=_python_code("print('no')"),
                cwd=tmp_path,
                env={"PYTHONIOENCODING": "utf-8"},
            )
        sup._finish_shutdown()

    def test_invalid_transition_is_logged(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup._state = ScraperState.RUNNING
        sup._set_state(ScraperState.IDLE)  # invalid: RUNNING -> IDLE
        assert sup.get_state() == ScraperState.RUNNING


class TestScraperProcessLifecycle:
    def test_success_exit_0(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code("print('done')"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert result.return_code == 0
        assert result.state == ScraperState.COMPLETED
        assert result.termination_reason == TerminationReason.NORMAL

    def test_exit_code_1(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code("sys.exit(1)"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert result.return_code == 1
        assert result.state == ScraperState.FAILED
        assert result.termination_reason == TerminationReason.SCRAPER_ERROR

    def test_captures_stdout_tail(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "for i in range(100): print(f'line {i}')"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert len(result.stdout_tail) > 0
        assert result.return_code == 0

    def test_captures_stderr(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import sys; sys.stderr.write('error msg\\n')"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert len(result.stderr_tail) > 0
        assert any("error msg" in l for l in result.stderr_tail)

    def test_tail_is_capped(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "for i in range(1000): print(f'line {i}', flush=True)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert len(result.stdout_tail) <= 100


class TestCancellation:
    def test_cancel_via_event(self, tmp_path):
        cancel = threading.Event()
        sup = ScraperProcessSupervisor()

        def delayed_cancel():
            time.sleep(0.3)
            cancel.set()

        t = threading.Thread(target=delayed_cancel, daemon=True)
        t.start()

        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import time\nwhile True:\n    print('alive', flush=True)\n    time.sleep(0.1)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            cancel_event=cancel,
        )
        assert result.termination_reason == TerminationReason.USER_CANCELLED
        assert result.state in (ScraperState.KILLED, ScraperState.STOPPED,
                                ScraperState.FAILED)

    def test_cancel_method(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup.start(
            executable=Path(sys.executable),
            args=_python_code(
                "import time\nwhile True:\n    print('alive', flush=True)\n    time.sleep(0.1)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        sup.cancel()
        result = sup.wait()
        assert result.termination_reason == TerminationReason.USER_CANCELLED


class TestTimeout:
    def test_timeout_terminates(self, tmp_path):
        sup = ScraperProcessSupervisor()
        start = time.monotonic()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import time\nwhile True:\n    print('alive', flush=True)\n    time.sleep(0.05)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            timeout=0.3,
        )
        duration = time.monotonic() - start
        assert result.termination_reason == TerminationReason.EXECUTION_TIMEOUT
        assert duration < 10

    def test_timeout_distinct_from_cancel(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import time\nwhile True:\n    time.sleep(0.1)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            timeout=0.2,
        )
        assert result.termination_reason == TerminationReason.EXECUTION_TIMEOUT
        assert result.termination_reason != TerminationReason.USER_CANCELLED


class TestDiagnostics:
    def test_get_diagnostics(self, tmp_path):
        sup = ScraperProcessSupervisor()
        diag = sup.get_diagnostics()
        assert diag.state == "IDLE"
        assert diag.pid is None

        sup.run(
            executable=Path(sys.executable),
            args=_python_code("print('ok')"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )

        diag = sup.get_diagnostics()
        assert diag.state in ("COMPLETED", "STOPPED")
        assert diag.return_code == 0


class TestReadersAndCleanup:
    def test_readers_terminate_after_run(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup.run(
            executable=Path(sys.executable),
            args=_python_code("print('ok')"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert len(sup._reader_threads) == 0

    def test_both_streams_read_concurrently(self, tmp_path):
        sup = ScraperProcessSupervisor()

        script = (
            "import sys, threading\n"
            "def w(stream):\n"
            "    for i in range(10):\n"
            "        print(f'{stream.name} {i}', file=stream, flush=True)\n"
            "t1 = threading.Thread(target=w, args=(sys.stdout,), daemon=True)\n"
            "t2 = threading.Thread(target=w, args=(sys.stderr,), daemon=True)\n"
            "t1.start(); t2.start()\n"
            "t1.join(); t2.join()\n"
        )

        result = sup.run(
            executable=Path(sys.executable),
            args=["-c", script],
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert result.return_code == 0
        assert len(result.stdout_tail) > 0
        assert len(result.stderr_tail) > 0


class TestProgressCallback:
    def test_progress_callback_receives_lines(self, tmp_path):
        received = []
        sup = ScraperProcessSupervisor()
        sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import json\n"
                "print(json.dumps({'level': 'info', 'message': '5 places found'}), file=sys.stderr)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            progress_callback=lambda p: received.append(p),
        )
        assert len(received) >= 1
        found = any(getattr(p, "places_found", 0) == 5 for p in received)
        assert found


class TestErrors:
    def test_errors_list(self, tmp_path):
        sup = ScraperProcessSupervisor()
        result = sup.run(
            executable=Path(sys.executable),
            args=_python_code(
                "import sys, json\n"
                "err = json.dumps({'level': 'error', 'message': 'connection failed'})\n"
                "print(err, file=sys.stderr)\n"
                "sys.exit(1)"
            ),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert len(result.errors) >= 1
        assert "connection failed" in result.errors[0]


class TestIsRunning:
    def test_is_running_returns_false_after_completion(self, tmp_path):
        sup = ScraperProcessSupervisor()
        assert not sup.is_running()
        sup.run(
            executable=Path(sys.executable),
            args=_python_code("print('ok')"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert not sup.is_running()

    def test_is_running_returns_true_during_execution(self, tmp_path):
        sup = ScraperProcessSupervisor()
        sup.start(
            executable=Path(sys.executable),
            args=_python_code("import time; time.sleep(2)"),
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
        )
        assert sup.is_running()
        sup._finish_shutdown()
