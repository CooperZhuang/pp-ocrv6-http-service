"""_resolve_device 的设备解析测试."""

import sys
import types

import ocr_service
import pytest


def test_onnxruntime_auto_is_cpu():
    # onnxruntime 引擎不 import paddle, 固定 cpu
    assert ocr_service._resolve_device("auto", "onnxruntime") == "cpu"


@pytest.mark.parametrize("engine", ["onnxruntime", "paddle"])
def test_explicit_device_is_passthrough(engine):
    assert ocr_service._resolve_device("gpu:0", engine) == "gpu:0"
    assert ocr_service._resolve_device("cpu", engine) == "cpu"


def test_default_engine_is_onnxruntime_cpu():
    assert ocr_service._resolve_device("auto") == "cpu"


def _fake_paddle(compiled: bool, devices: int) -> object:
    fake = types.ModuleType("paddle")
    fake.is_compiled_with_cuda = lambda: compiled
    device_ns = types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda: devices))
    fake.device = device_ns
    return fake


def test_paddle_auto_with_cuda_returns_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddle", _fake_paddle(True, 1))
    assert ocr_service._resolve_device("auto", "paddle") == "gpu:0"


def test_paddle_auto_no_cuda_returns_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddle", _fake_paddle(False, 0))
    assert ocr_service._resolve_device("auto", "paddle") == "cpu"


def test_paddle_auto_import_error_returns_cpu(monkeypatch):
    # paddle 缺 is_compiled_with_cuda -> AttributeError 被捕获 -> cpu
    fake = types.ModuleType("paddle")
    monkeypatch.setitem(sys.modules, "paddle", fake)
    assert ocr_service._resolve_device("auto", "paddle") == "cpu"
