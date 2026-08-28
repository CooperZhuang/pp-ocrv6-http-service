"""配置文件加载与路径解析测试."""

import json
import os

import ocr_service


def test_load_valid_json(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"port": 1234}), encoding="utf-8")
    assert ocr_service._load_config(str(p)) == {"port": 1234}


def test_load_missing_file_returns_empty(tmp_path):
    assert ocr_service._load_config(str(tmp_path / "nope.json")) == {}


def test_load_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert ocr_service._load_config(str(p)) == {}


def test_load_empty_string_returns_empty(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    assert ocr_service._load_config(str(p)) == {}


def test_resolve_config_path_relative_points_to_script_dir():
    script_dir = os.path.dirname(os.path.abspath(ocr_service.__file__))
    resolved = ocr_service._resolve_config_path("config.json")
    assert os.path.dirname(resolved) == script_dir


def test_resolve_config_path_absolute_passthrough(tmp_path):
    p = tmp_path / "abs.json"
    assert ocr_service._resolve_config_path(str(p)) == str(p)
