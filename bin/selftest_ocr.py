#!/usr/bin/env python
"""部署自检 (deployment self-test) for the PP-OCRv6 HTTP OCR service.

由 ``bin\\selftest_ocr.bat`` 一键调用; 也可直接运行::

    .venv\\Scripts\\python.exe bin\\selftest_ocr.py [图片或PDF] [--port 18010]

检查项:

    [1/4] Python 版本 + 运行依赖 (fastapi/uvicorn/onnxruntime/paddleocr...)
    [2/4] 模型缓存状态 (缺失时首次识别会联网下载, 约 146MB)
    [3/4] 真实识别一张图/PDF, 把识别文本打印到命令行
    [4/4] HTTP 端到端探测 (仅当服务已在监听; 未启动则跳过, 不算失败)

退出码: 0 = PASS, 1 = FAIL (任一必需项失败)。HTTP 项为 SKIP 时不影响退出码。
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# 抑制 onnxruntime / glog 在加载模型时的内部告警噪声
os.environ.setdefault("GLOG_minloglevel", "2")

# 默认样图 (随仓库提交, 34KB 电子发票); 识别结果应含 "发票" 二字
DEFAULT_INPUT = REPO / "tests" / "fixtures" / "sample_invoice.png"
DEFAULT_EXPECT = "发票"

# 默认 onnxruntime 引擎真正需要的 4 个 ONNX 模型目录 (共约 146MB)
REQUIRED_MODELS = (
    "PP-OCRv6_medium_det_onnx",
    "PP-OCRv6_medium_rec_onnx",
    "PP-LCNet_x1_0_textline_ori_onnx",
    "PP-LCNet_x1_0_doc_ori_onnx",
)
REQUIRED_PACKAGES = (
    "fastapi",
    "uvicorn",
    "python-multipart",
    "onnxruntime",
    "paddleocr",
    "paddlex",
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _say(text: str = "") -> None:
    print(text, flush=True)


def _step(no: int, total: int, title: str) -> None:
    _say()
    _say(f"[{no}/{total}] {title}")
    _say("-" * 62)


def check_environment() -> tuple[bool, list[str]]:
    """校验 Python 版本与运行依赖; 返回 (是否通过, 明细行)."""
    lines = []
    ok = True
    version = ".".join(str(x) for x in sys.version_info[:3])
    minor = sys.version_info[:2]
    supported = (3, 11) <= minor < (3, 14)
    lines.append(f"Python {version} ({sys.executable})")
    if minor != (3, 11):
        lines.append("  ! 本项目在 CPython 3.11 上验证 (CI/本地一致); 推荐 3.11")
    if not supported:
        ok = False
        lines.append("  ! 需要 Python 3.11 ~ 3.13")
    for name in REQUIRED_PACKAGES:
        try:
            lines.append(f"{name:<18} {md.version(name)}")
        except md.PackageNotFoundError:
            ok = False
            lines.append(f"{name:<18} 缺失 -> 先运行 bin\\deploy_env.bat")
    try:
        import ocr_service  # noqa: F401

        lines.append(f"{'ocr_service':<18} import OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"{'ocr_service':<18} import 失败: {exc}")
    for line in lines:
        _say("  " + line)
    return ok, lines


def check_models() -> tuple[bool, str]:
    """检查模型缓存; 返回 (是否已就绪, 说明)."""
    cache = REPO / "models" / "official_models"
    missing = [name for name in REQUIRED_MODELS if not (cache / name).is_dir()]
    for name in REQUIRED_MODELS:
        marker = "OK     " if (cache / name).is_dir() else "缺失   "
        _say(f"  [{marker}] {name}")
    _say(f"  缓存目录: {cache}")
    if missing:
        note = f"缺少 {len(missing)} 个模型 -> 首次识别会联网下载 (约 146MB)"
        _say(f"  ! {note}")
        return False, note
    _say("  模型缓存完整 (离线可用)")
    return True, "模型缓存完整"


def recognize(path: Path, expect: str | None) -> tuple[bool, str]:
    """用真实模型识别一张图/PDF, 把结果打印到命令行."""
    if not path.is_file():
        _say(f"  ! 找不到输入文件: {path}")
        return False, f"输入文件不存在: {path}"
    import onnxruntime

    # 只报 ERROR: 加载模型时会刷 "Removing initializer ..." 这类 W 级内部告警
    onnxruntime.set_default_logger_severity(3)

    import ocr_service

    _say(f"  输入: {path}  ({path.stat().st_size / 1024:.0f} KB)")
    _say("  引擎: onnxruntime / device=cpu  (首次加载模型需数秒)")
    started = time.perf_counter()
    try:
        pages = ocr_service.ocr_file(path, engine="onnxruntime", device="cpu")
    except Exception as exc:  # noqa: BLE001
        _say(f"  ! 识别抛异常: {exc}")
        return False, f"识别异常: {exc}"
    elapsed = time.perf_counter() - started
    total_lines = 0
    for page in pages:
        texts = page.get("texts") or []
        total_lines += len(texts)
        heads = [f"第 {page.get('page_index', 0)} 页", f"{len(texts)} 行"]
        _say(f"  -- {' / '.join(heads)} --")
        for line in page.get("lines", []):
            _say(f"     [{line.get('score', 0.0):.2f}] {line.get('text', '')}")
    _say(f"  合计 {total_lines} 行, 用时 {elapsed:.2f}s")
    if total_lines == 0:
        return False, "识别结果为空 (0 行)"
    if expect and expect not in "".join(t for p in pages for t in (p.get("texts") or [])):
        return False, f"识别结果未包含期望文本 {expect!r}"
    return True, f"识别成功: {total_lines} 行 / {elapsed:.2f}s"


def _service_port(cli_port: int | None) -> int:
    if cli_port:
        return cli_port
    cfg = REPO / "config.json"
    try:
        return int(json.loads(cfg.read_text(encoding="utf-8")).get("port", 18010))
    except (OSError, ValueError, json.JSONDecodeError):
        return 18010


def _is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def _post_ocr(port: int, path: Path, timeout: float = 600.0) -> dict[str, Any]:
    """multipart/form-data 上传单个文件到 POST /ocr (只用标准库, 无需 requests)."""
    boundary = "----ppocrv6selftestboundary"
    suffix = path.suffix.lower() or ".bin"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="files"; '
                f'filename="selftest_input{suffix}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/ocr",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def probe_http(port: int, path: Path) -> tuple[str, str]:
    """探测已运行的 HTTP 服务; 返回 (PASS/FAIL/SKIP, 说明)."""
    if not _is_listening(port):
        _say(f"  127.0.0.1:{port} 未在监听 -> 跳过 (启动: bin\\start_http.bat)")
        return SKIP, f"服务未启动 (端口 {port} 无监听)"
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"http://127.0.0.1:{port}/health", timeout=10
        ) as response:
            health = json.loads(response.read().decode("utf-8"))
        _say(
            "  GET /health -> "
            f"ok={health.get('ok')} engine={health.get('engine')} "
            f"device={health.get('device')} model_loaded={health.get('model_loaded')}"
        )
        started = time.perf_counter()
        payload = _post_ocr(port, path)
        elapsed = time.perf_counter() - started
        results = payload.get("results") or []
        for item in results:
            pages = item.get("pages") or []
            lines = sum(len(p.get("texts") or []) for p in pages)
            _say(
                f"  POST /ocr   -> {item.get('filename')} ok={item.get('ok')} "
                f"pages={len(pages)} lines={lines} 用时={elapsed:.2f}s"
            )
            for page in pages[:1]:
                for line in (page.get("lines") or [])[:5]:
                    _say(f"     [{line.get('score', 0.0):.2f}] {line.get('text', '')}")
            if not item.get("ok"):
                return FAIL, f"HTTP 识别失败: {item.get('error')}"
        if not results:
            return FAIL, "HTTP 响应里没有 results"
        return PASS, f"HTTP 端到端成功 ({elapsed:.2f}s)"
    except Exception as exc:  # noqa: BLE001
        return FAIL, f"HTTP 探测异常: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PP-OCRv6 部署自检: 环境 + 模型 + 真实识别 (+ 可选 HTTP 探测)"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=f"要识别的图片/PDF, 默认 {DEFAULT_INPUT.relative_to(REPO)}",
    )
    parser.add_argument("--port", type=int, help="HTTP 探测端口, 默认取 config.json 的 port")
    parser.add_argument("--no-http", action="store_true", help="跳过 HTTP 端到端探测")
    parser.add_argument("--expect", help="期望识别结果包含的文本 (默认: 样图要求包含「发票」)")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    path = Path(args.inputs[0]).resolve() if args.inputs else DEFAULT_INPUT
    expect = args.expect
    if expect is None and path == DEFAULT_INPUT:
        expect = DEFAULT_EXPECT

    _say("=" * 62)
    _say(" PP-OCRv6 HTTP OCR service - 部署自检")
    _say(f" 项目目录: {REPO}")
    _say("=" * 62)

    results: list[tuple[str, str]] = []

    _step(1, 4, "Python / 运行依赖")
    env_ok, _ = check_environment()
    results.append((PASS if env_ok else FAIL, "Python / 依赖"))

    _step(2, 4, "模型缓存")
    models_ready, model_note = check_models()
    results.append((PASS if models_ready else SKIP, f"模型缓存 ({model_note})"))

    _step(3, 4, "真实识别 (命令行输出识别结果)")
    rec_ok, rec_note = recognize(path, expect)
    results.append((PASS if rec_ok else FAIL, f"识别 ({rec_note})"))

    if args.no_http:
        _step(4, 4, "HTTP 端到端探测")
        _say("  --no-http: 跳过")
        results.append((SKIP, "HTTP 探测 (--no-http)"))
    else:
        _step(4, 4, f"HTTP 端到端探测 (127.0.0.1:{_service_port(args.port)})")
        http_state, http_note = probe_http(_service_port(args.port), path)
        results.append((http_state, f"HTTP 探测 ({http_note})"))

    _say()
    _say("=" * 62)
    _say(" 自检结果")
    _say("=" * 62)
    for state, label in results:
        _say(f"  [{state:^4}] {label}")
    failed = [label for state, label in results if state == FAIL]
    _say("=" * 62)
    if failed:
        _say(f" 结果: FAIL ({len(failed)} 项失败) -> {', '.join(failed)}")
        return 1
    _say(" 结果: PASS - 部署可用, OCR 识别正常")
    _say(" 下一步: bin\\start_http.bat 启动服务, bin\\install_scheduled_task.bat 开机自启")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
