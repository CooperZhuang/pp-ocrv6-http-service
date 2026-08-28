"""pytest 共享配置与 fixture.

- 把仓库根目录加入 sys.path, 便于 `import ocr_service`.
- 提供测试用 double: FakeWorker(不带真实模型/GPU), 供端点契约测试.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr_service  # noqa: E402
import pytest  # noqa: E402


class FakeWorker:
    """最小的 worker double: 暴露 build_app 需要的接口, 不加载真实模型.

    用于测试 HTTP 端点契约(health/ocr 响应结构、错误语义), 与真实推理解耦.
    """

    device = "cpu"
    engine = "onnxruntime"

    def __init__(self, fail: bool = False) -> None:
        self._loaded = False
        self._fail = fail

    @property
    def model_loaded(self) -> bool:
        return self._loaded

    def process(self, path: str) -> list:
        self._loaded = True
        if self._fail:
            raise RuntimeError("boom")
        return [
            {
                "input_path": path,
                "page_index": 0,
                "texts": ["电子发票"],
                "scores": [1.0],
                "boxes": [[0, 0, 1, 1]],
                "polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                "lines": [
                    {"text": "电子发票", "score": 1.0, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}
                ],
            }
        ]

    def shutdown(self) -> None:
        pass


@pytest.fixture
def fake_worker() -> FakeWorker:
    return FakeWorker()


@pytest.fixture
def app(fake_worker: FakeWorker):
    return ocr_service.build_app(fake_worker)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
