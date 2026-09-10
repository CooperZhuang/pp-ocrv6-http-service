"""_WorkerEngine(子进程隔离) 的单元测试: 用 mock 子进程, 不加载真实模型."""

import time
from unittest.mock import patch

import ocr_service
import pytest
from ocr_service import _WorkerEngine

READY = '{"id": 0, "ready": true}'
OK = (
    '{"id": 1, "ok": true, "pages": [{"input_path": "x.png", "page_index": 0, '
    '"texts": ["a"], "scores": [1.0], "boxes": [[0,0,1,1]], '
    '"polys": [[[0,0],[1,0],[1,1],[0,1]]], "lines": []}]}'
)


class _LineReader:
    """按顺序吐出预置行; 用尽后返回 ''(EOF, 模拟子进程已死)."""

    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _FakeStdin:
    def write(self, s):  # noqa: ARG002
        pass

    def flush(self):
        pass


class FakeProc:
    def __init__(self, stdout_lines):
        self._lines = list(stdout_lines)
        self._exit_code = None
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _LineReader(self._lines)

    def poll(self):
        return self._exit_code

    def kill(self):
        self.killed = True
        self._exit_code = 1

    def wait(self, timeout=None):  # noqa: ARG002
        return self._exit_code


@pytest.fixture
def worker():
    return _WorkerEngine(engine="onnxruntime", device="cpu", idle_unload_s=10)


def test_process_spawns_and_returns_pages(worker):
    proc = FakeProc(stdout_lines=[READY, OK])
    with patch.object(ocr_service.subprocess, "Popen", return_value=proc) as m:
        pages = worker.process("x.png")
        assert pages[0]["texts"] == ["a"]
        m.assert_called_once()
        assert worker.model_loaded is True


def test_process_respawns_when_child_died(worker):
    dead = FakeProc(stdout_lines=[READY])  # 第一行 ready, 之后 readline 空 -> 死
    alive = FakeProc(stdout_lines=[READY, OK])
    with patch.object(ocr_service.subprocess, "Popen", side_effect=[dead, alive]) as m:
        pages = worker.process("x.png")
    assert pages[0]["texts"] == ["a"]
    assert m.call_count == 2


def test_process_raises_when_child_fails(worker):
    proc = FakeProc(stdout_lines=[READY, '{"id":1,"ok":false,"error":"model fail"}'])
    with (
        patch.object(ocr_service.subprocess, "Popen", return_value=proc),
        pytest.raises(RuntimeError, match="model fail"),
    ):
        worker.process("x.png")


def test_spawn_passes_det_limit_when_set():
    """设了 det_limit_side_len -> 推理子进程命令行带上该参数(票据小字识别用)."""
    w = _WorkerEngine(engine="onnxruntime", device="cpu", idle_unload_s=10,
                      det_limit_side_len=1600)
    proc = FakeProc(stdout_lines=[READY, OK])
    with patch.object(ocr_service.subprocess, "Popen", return_value=proc) as m:
        w.process("x.png")
    cmd = m.call_args[0][0]
    assert "--det-limit-side-len" in cmd
    assert cmd[cmd.index("--det-limit-side-len") + 1] == "1600"


def test_spawn_omits_det_limit_by_default(worker):
    """没配就不传 -> 沿用 PaddleOCR 默认 960, 行为与改造前一致."""
    proc = FakeProc(stdout_lines=[READY, OK])
    with patch.object(ocr_service.subprocess, "Popen", return_value=proc) as m:
        worker.process("x.png")
    assert "--det-limit-side-len" not in m.call_args[0][0]


def test_unload_if_idle_kills_after_timeout(worker):
    proc = FakeProc(stdout_lines=[READY])
    worker._proc = proc  # type: ignore[assignment]
    worker._last_use = time.monotonic() - 20  # 空闲超时(阈值 10s)
    assert worker.unload_if_idle() is True
    assert proc.killed is True
    assert worker.model_loaded is False


def test_unload_if_idle_keeps_before_timeout(worker):
    proc = FakeProc(stdout_lines=[READY])
    worker._proc = proc  # type: ignore[assignment]
    worker._last_use = time.monotonic()  # 刚用过
    assert worker.unload_if_idle() is False
    assert proc.killed is False


def test_unload_if_idle_disabled_when_zero():
    w = _WorkerEngine(engine="onnxruntime", device="cpu", idle_unload_s=0)
    proc = FakeProc(stdout_lines=[READY])
    w._proc = proc  # type: ignore[assignment]
    w._last_use = time.monotonic() - 1000
    assert w.unload_if_idle() is False  # idle_unload_s<=0 不杀
    assert proc.killed is False


def test_model_loaded_reflects_alive(worker):
    proc = FakeProc(stdout_lines=[READY])
    worker._proc = proc  # type: ignore[assignment]
    assert worker.model_loaded is True
    worker._kill()
    assert worker.model_loaded is False
