"""OcrEngine 序列化辅助的单元测试(纯逻辑, 不加载模型)."""

import ocr_service

E = ocr_service.OcrEngine
_ENGINE = E.__new__(E)  # 跳过 __init__, 只测纯序列化方法


class _FakePage:
    def __init__(self, texts, scores, polys, boxes, path="a.png", idx=0):
        self.rec_texts = texts
        self.rec_scores = scores
        self.rec_polys = polys
        self.rec_boxes = boxes
        self.input_path = path
        self.page_index = idx


class _FakeNumpy:
    """模拟 numpy 标量/数组: 有 tolist() / item()."""

    def __init__(self, value):
        self._value = value

    def tolist(self):
        if isinstance(self._value, (list, tuple)):
            return list(self._value)
        return self._value

    def item(self):
        return self._value


def test_to_list_various_types():
    assert E._to_list(None) == []
    assert E._to_list([1, 2, 3]) == [1, 2, 3]
    assert E._to_list((1, 2)) == [1, 2]
    assert E._to_list([[1, 2], [3, 4]]) == [[1, 2], [3, 4]]


def test_to_list_numpy_like():
    assert E._to_list(_FakeNumpy(5)) == 5
    assert E._to_list(_FakeNumpy([1, 2])) == [1, 2]


def test_page_value_dict_and_attribute():
    assert E._page_value({"a": 1}, "a") == 1
    assert E._page_value({"a": None}, "a", default=9) == 9


class _Obj:
    b = 3


def test_page_value_object_attribute():
    assert E._page_value(_Obj(), "b", default=0) == 3
    assert E._page_value(_Obj(), "missing", default=0) == 0


def test_serialize_page_structure_and_lines():
    page = _FakePage(
        texts=["T1", "T2"],
        scores=[0.9, 0.8],
        polys=[[[0, 0], [1, 0], [1, 1], [0, 1]], [[2, 0], [3, 0], [3, 1], [2, 1]]],
        boxes=[[0, 0, 1, 1], [2, 0, 3, 1]],
        path="a.png",
        idx=3,
    )
    out = _ENGINE._serialize_page(page)
    assert out["page_index"] == 3
    assert out["input_path"] == "a.png"
    assert out["texts"] == ["T1", "T2"]
    assert out["scores"] == [0.9, 0.8]
    assert out["boxes"] == [[0, 0, 1, 1], [2, 0, 3, 1]]
    assert len(out["lines"]) == 2
    assert out["lines"][0]["text"] == "T1"
    assert out["lines"][0]["score"] == 0.9
    assert out["lines"][0]["box"] == page.rec_polys[0]


def test_serialize_page_box_fallback():
    # 缺 polys 时用 boxes 兜底
    page = _FakePage(
        texts=["X"],
        scores=[0.5],
        polys=[[[9, 9], [9, 9], [9, 9], [9, 9]]],
        boxes=[[1, 2, 3, 4]],
        idx=0,
    )
    out = _ENGINE._serialize_page(page)
    assert out["lines"][0]["box"] == page.rec_polys[0]


def test_serialize_page_score_fallback_when_missing():
    page = _FakePage(texts=["A", "B"], scores=[0.7], polys=[[], []], boxes=[], idx=0)
    out = _ENGINE._serialize_page(page)
    # 第二个 line 没有对应 score -> 0.0
    assert out["lines"][1]["score"] == 0.0
