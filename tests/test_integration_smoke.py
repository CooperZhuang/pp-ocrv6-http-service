"""冒烟/集成测试: 用真实模型(ONNX Runtime CPU)跑真实发票样图.

标记 @integration, 默认被 pyproject addopts `-m 'not integration'` 排除;
用 `pytest -m integration` 运行. 首次运行需联网(自动下载 ONNX 模型).

覆盖:
  - 子进程隔离引擎真实推理一张发票, 结果含发票文本.
  - 加载后子进程存活; 空闲超过 idle_unload_s 后自动卸载, 子进程被杀(内存释放).
"""

import os
import time

import ocr_service
import pytest

SAMPLE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_invoice.png")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="缺少冒烟测试样图")
def test_onnxruntime_cpu_smoke_and_idle_unload():
    """真实推理 + 空闲卸载(释放内存)的端到端冒烟."""
    worker = ocr_service._WorkerEngine(engine="onnxruntime", device="cpu", idle_unload_s=2)
    try:
        pages = worker.process(SAMPLE)
        assert pages, "应返回至少一页结果"
        joined = "".join(pages[0]["texts"])
        assert "发票" in joined, f"识别结果应含发票文本, got {pages[0]['texts'][:5]}"

        # 加载后子进程(模型)应存活
        assert worker.is_alive is True

        # 空闲超过 idle_unload_s 秒 -> 自动卸载, 子进程被杀(内存归还系统)
        time.sleep(2.5)
        assert worker.unload_if_idle() is True
        assert worker.is_alive is False

        # 卸载后再次请求 -> 重新冷启动子进程仍能识别(懒加载/再加载)
        pages2 = worker.process(SAMPLE)
        assert "发票" in "".join(pages2[0]["texts"])
    finally:
        worker.shutdown()
