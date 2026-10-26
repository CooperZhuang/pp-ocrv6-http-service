#!/usr/bin/env python3
"""PP-OCRv6 通用 OCR 服务封装.

基于 PaddleOCR 3.7.0 + PP-OCRv6_medium 模型，支持 CPU/GPU 推理。
封装为可复用、可脚本化的服务，供其他项目直接 import 或通过命令行调用。

设计要点:
  - 默认引擎 onnxruntime: 无需 paddlepaddle / CUDA, 任何 Windows 机器可跑(CPU 约 1.9s);
    可通过 engine='paddle' 切回 Paddle 后端(配合 GPU 更快).
  - 设备 auto: onnxruntime 默认 cpu; paddle 有可用 CUDA 则 gpu:0, 否则自动退回 cpu.
  - HTTP 公共服务模式 (serve_http) 用 [子进程隔离] 跑推理: 父进程仅 fastapi/uvicorn(~50MB),
    模型放进子进程(复用 serve stdin 桥), 空闲超过 idle_unload_s 秒直接杀掉子进程,
    模型与其库代码的内存全部归还系统, 父进程回到 ~50MB —— 极省内存, 适合 8GB 机器.
    代价: 每次空闲后首个请求要等子进程冷启动(导入库+载模型约 3~5s).
  - 配置从 config.json 读取(host/port/engine/device/idle_unload_s), CLI 参数优先.
  - OcrEngine(模块/CLI 用法) 懒加载单例: 创建不载模型, 首次 process 才创建预测器.
  - 对 PaddleOCR 的 ndarray 结果做 JSON 友好序列化, 便于直接写库或输出 JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# 模型缓存到仓库目录 models/ (PaddleX 用 PADDLE_PDX_CACHE_HOME 决定缓存根,
# 模型落在 <缓存根>/official_models/<模型名>). 缓存随项目走, 不散落在用户主目录.
_MODEL_CACHE_DIR = Path(__file__).resolve().parent / "models"
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(_MODEL_CACHE_DIR))
# 缓存已存在时跳过模型源连通性探测(减少启动等待); 首次(空缓存)保留探测以正常下载
_official = _MODEL_CACHE_DIR / "official_models"
if _official.is_dir() and any(_official.iterdir()):
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

__all__ = ["OcrEngine", "ocr", "ocr_file", "ocr_json", "serve", "serve_http", "SUPPORTED_EXTS"]


# 可直接交给 PaddleOCR 的输入扩展名 (PDF 由 paddlex 内部渲染)
SUPPORTED_EXTS = {
    ".pdf",
    ".ofd",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
}

# 模型自动保存位置 (PaddleX 官方模型目录), 首次运行自动下载
# 可通过环境变量 PADDLE_PDX_CACHE_HOME / PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK 控制
_MODEL_CACHE_ENV_HINT = (
    "models\\official_models\\PP-OCRv6_medium_{det,rec} + PP-LCNet_x1_0_textline_ori"
)

_DEFAULT_CONFIG_PATH = "config.json"
_IDLE_CHECK_INTERVAL = 5.0  # 空闲卸载检查周期(秒)

# 同一实例并发 predict 不保证线程安全, 用锁串行化推理; 子进程读写在锁内进行
_OCR_LOCK = threading.Lock()

_CFG_KEYS = (
    "host",
    "port",
    "engine",
    "device",
    "idle_unload_s",
    "det_limit_side_len",
    "timeout_keep_alive",
)

# keep-alive 连接空闲超时(秒). uvicorn 自己的默认值是 5s: 调用方(票据AI识别)每线程复用一个
# keep-alive 连接, 空闲超过 5s 服务端就发 FIN 关连接; 客户端若在 FIN 已发出、自己还没察觉的
# 毫秒级窗口里发 POST, 就会撞上一条已经关闭的连接(Windows 客户端表现为 10054 RST).
# 2026-10-24 生产出现过这种失败(0.34%), 因此默认放宽到 120s.
TIMEOUT_KEEP_ALIVE_DEFAULT = 120

# 文本检测输入边长上限(PaddleOCR 的 text_det_limit_side_len, 默认 960).
# 整页送进来时检测阶段会先把长边缩到该值——票据上的小字(手写成本中心/泛微号)会因此变糊。
# 调大能改善小字检出, 代价是 CPU 推理耗时随像素数上升(见 README「识别精度与耗时调参」).
DET_LIMIT_SIDE_LEN_DEFAULT = 960


def _resolve_device(device: str, engine: str = "onnxruntime") -> str:
    """把用户 device 解析为实际运行设备. 'auto' 时按引擎自动探测.

    onnxruntime 引擎默认面向 CPU 通用部署(无 CUDA/无 paddle 也能跑) -> cpu;
    paddle 引擎 auto: 有可用 CUDA 则 gpu:0, 否则退回 cpu.
    """
    if device and device != "auto":
        return device
    if engine == "onnxruntime":
        return "cpu"
    try:
        import paddle

        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            return "gpu:0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _resolve_timeout_keep_alive(value: Any) -> float:
    """校验 timeout_keep_alive: 只接受有限正数(秒), 非法值退回默认并告警.

    0/负数/NaN/Inf/非数字都无法让 uvicorn 正常表现, 直接退回默认 120s,
    避免把非法值原样传给 uvicorn 后连接行为不可预期.
    """
    try:
        val = float(value)
    except (TypeError, ValueError):
        _LOG.warning("timeout_keep_alive 非法(%r), 使用默认 %ss", value, TIMEOUT_KEEP_ALIVE_DEFAULT)
        return float(TIMEOUT_KEEP_ALIVE_DEFAULT)
    if not math.isfinite(val) or val <= 0:
        _LOG.warning(
            "timeout_keep_alive 必须是正数(%r), 使用默认 %ss", value, TIMEOUT_KEEP_ALIVE_DEFAULT
        )
        return float(TIMEOUT_KEEP_ALIVE_DEFAULT)
    return val


def _load_config(path: str) -> dict[str, Any]:
    """读取 JSON 配置文件; 不存在或解析失败时返回空字典."""
    p = Path(path)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _resolve_config_path(path: str) -> str:
    """把相对配置路径解析到脚本目录, 使 cwd 不影响 config.json 定位."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(os.path.dirname(os.path.abspath(__file__))) / p
    return str(p)


_LOG = logging.getLogger("ocr_service")  # 本服务通用日志器

# 日志轮转默认参数 (可按 config.json 的 log_max_bytes/log_backup_count 覆盖)
_LOG_DEFAULT_PATH = "logs/ocr_service.log"
_LOG_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_LOG_DEFAULT_BACKUP = 3


def _setup_logging(
    level: str = "info",
    log_file: str | None = None,
    max_bytes: int = _LOG_DEFAULT_MAX_BYTES,
    backup_count: int = _LOG_DEFAULT_BACKUP,
) -> None:
    """配置根日志器: 日志写到 log_file(默认 logs/ocr_service.log, 按大小轮转);
    若存在真实控制台(非无窗口后台)再加一个控制台输出, 便于调试."""
    path = log_file or _resolve_config_path(_LOG_DEFAULT_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # 清掉可能已有的 handler, 避免重复
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 真实控制台时额外输出一份, 方便看日志
    if getattr(sys.stderr, "name", "") != "<stderr>":
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


class OcrEngine:
    """PP-OCRv6 OCR 引擎封装(懒加载单例) — 模块/CLI 用法.

    懒加载: 构造时不创建预测器; 首次 process() 才加载模型。
    (HTTP 服务改用 _WorkerEngine 子进程隔离, 不直接使用本类.)
    """

    _instance: OcrEngine | None = None

    def __init__(
        self,
        device: str = "auto",
        engine: str = "onnxruntime",
        enable_mkldnn: bool = False,
        use_doc_orientation_classify: bool = True,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = True,
        text_det_limit_side_len: int | None = None,
        text_rec_score_thresh: float = 0.0,
        lang: str = "ch",
    ) -> None:
        device = _resolve_device(device, engine)
        print(f"[ocr] 实际运行设备: {device}, 引擎: {engine}", file=sys.stderr)
        self.device = device
        self.engine = engine
        self.lang = lang
        self._params = {
            "device": device,
            "engine": engine,
            "enable_mkldnn": enable_mkldnn,
            "use_doc_orientation_classify": use_doc_orientation_classify,
            "use_doc_unwarping": use_doc_unwarping,
            "use_textline_orientation": use_textline_orientation,
            "text_rec_score_thresh": text_rec_score_thresh,
            "lang": lang,
        }
        if text_det_limit_side_len is not None:
            self._params["text_det_limit_side_len"] = text_det_limit_side_len
        self._ocr: Any | None = None  # 懒加载: 首次推理才创建

    @property
    def model_loaded(self) -> bool:
        return self._ocr is not None

    def _ensure_ocr(self) -> Any:
        if self._ocr is None:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(**self._params)
        return self._ocr

    def process(self, input_path: str | Path) -> list[dict[str, Any]]:
        """识别单张票据图片/PDF, 返回页面结果列表. 首次调用触发懒加载."""
        input_path = Path(input_path)
        if not input_path.is_file():
            raise FileNotFoundError(f"输入文件不存在: {input_path}")
        ocr = self._ensure_ocr()
        pages: list[dict[str, Any]] = []
        for page in ocr.predict(str(input_path)):
            pages.append(self._serialize_page(page))
        return pages

    # ------------------------------------------------------------- 序列化辅助
    @staticmethod
    def _to_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if hasattr(value, "tolist"):
            return OcrEngine._to_list(value.tolist())
        if isinstance(value, (list, tuple)):
            return [OcrEngine._to_list(v) for v in value]
        try:
            if hasattr(value, "item"):
                return value.item()
        except Exception:
            pass
        return value

    @staticmethod
    def _page_value(page: Any, key: str, default: Any = None) -> Any:
        if isinstance(page, dict):
            val = page.get(key, default)
            return default if val is None else val
        try:
            val = page[key]
        except (KeyError, TypeError):
            val = getattr(page, key, default)
        return default if val is None else val

    def _serialize_page(self, page: Any) -> dict[str, Any]:
        texts = self._to_list(self._page_value(page, "rec_texts"))
        scores = self._to_list(self._page_value(page, "rec_scores"))
        polys = self._to_list(self._page_value(page, "rec_polys"))
        boxes = self._to_list(self._page_value(page, "rec_boxes"))
        lines = []
        for i, text in enumerate(texts):
            lines.append(
                {
                    "text": str(text),
                    "score": float(scores[i]) if i < len(scores) else 0.0,
                    "box": polys[i] if i < len(polys) else (boxes[i] if i < len(boxes) else []),
                }
            )
        return {
            "input_path": str(self._page_value(page, "input_path", "")),
            "page_index": int(self._page_value(page, "page_index", 0)),
            "texts": texts,
            "scores": scores,
            "boxes": boxes,
            "polys": polys,
            "lines": lines,
        }


def ocr(device: str = "auto", engine: str = "onnxruntime", **kwargs) -> OcrEngine:
    """获取懒加载的全局引擎实例(模块/CLI 用法, 线程安全度由单例保证)."""
    if OcrEngine._instance is None:
        OcrEngine._instance = OcrEngine(device=device, engine=engine, **kwargs)
    return OcrEngine._instance


def ocr_file(input_path: str | Path, **kwargs) -> list[dict[str, Any]]:
    return ocr(**kwargs).process(input_path)


def ocr_json(input_path: str | Path, indent: int = 2, **kwargs) -> str:
    return json.dumps(ocr_file(input_path, **kwargs), ensure_ascii=False, indent=indent)


# ======================================================================
#  子进程隔离推理引擎: 模型放进子进程, 空闲超时杀掉子进程, 内存全部归还系统.
# ======================================================================
class _WorkerEngine:
    """HTTP 服务专用: 推理放到子进程(复用 serve stdin 桥), 空闲超时杀掉子进程.

    父进程仅持有 fastapi/uvicorn(~50MB); 子进程承载 onnxruntime/paddleocr + 模型.
    空闲超过 idle_unload_s 秒后 kill 子进程, 子进程连同其库代码/模型内存全部归还 OS,
    父进程回到 ~50MB. 首请求会冷启动子进程(约 3~5s).
    """

    def __init__(
        self,
        engine: str = "onnxruntime",
        device: str = "auto",
        idle_unload_s: float = 60.0,
        det_limit_side_len: int | None = None,
    ) -> None:
        self.engine = engine
        self.device = _resolve_device(device, engine)
        self.idle_unload_s = float(idle_unload_s)
        # None = 不传该参数(沿用 PaddleOCR 默认 960); 显式给值则透传给推理子进程
        self.det_limit_side_len = det_limit_side_len
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_use = time.monotonic()
        self._req_id = 0

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self) -> None:
        """启动推理子进程(serve stdin 桥模式)并等待就绪握手."""
        script = os.path.abspath(__file__)
        repo = os.path.dirname(script)
        cmd = [sys.executable, script, "serve", "--engine", self.engine, "--device", self.device]
        if self.det_limit_side_len is not None:
            cmd += ["--det-limit-side-len", str(int(self.det_limit_side_len))]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=repo,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # 等待就绪握手 {"id":0,"ready":true}
        try:
            line = self._proc.stdout.readline()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            line = ""
        if "ready" not in line:
            _LOG.error("推理子进程启动失败 (engine=%s device=%s)", self.engine, self.device)
            self._kill()
            raise RuntimeError("推理子进程启动失败")
        _LOG.info("推理子进程已启动 (engine=%s device=%s det_limit=%s)",
                  self.engine, self.device, self.det_limit_side_len or "默认960")

    def _kill(self) -> None:
        """终止当前子进程(若有). 调用方需持有 self._lock."""
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=5)
            self._proc = None
            _LOG.info("推理子进程已终止, 模型内存已释放")

    @property
    def model_loaded(self) -> bool:
        return self.is_alive

    def process(self, path: str) -> list[dict[str, Any]]:
        """经子进程做一次推理; 返回页面结果列表. 调用方在 _OCR_LOCK 外自行串行."""
        with self._lock:
            self._last_use = time.monotonic()
            last_err = ""
            for _ in range(2):
                if not self.is_alive:
                    self._spawn()
                self._req_id += 1
                req = json.dumps({"id": self._req_id, "path": str(path)}) + "\n"
                try:
                    self._proc.stdin.write(req)  # type: ignore[union-attr]
                    self._proc.stdin.flush()  # type: ignore[union-attr]
                    line = self._proc.stdout.readline()  # type: ignore[union-attr]
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                    self._kill()
                    continue
                if not line.strip():
                    last_err = "推理子进程意外退出"
                    self._kill()
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    self._kill()
                    continue
                if resp.get("ok"):
                    return resp.get("pages", [])
                last_err = resp.get("error", "推理失败")
                raise RuntimeError(last_err)
            raise RuntimeError(last_err or "推理子进程失败")

    def unload_if_idle(self) -> bool:
        """空闲超时则杀掉子进程, 返回是否已卸载."""
        with self._lock:
            if not self.is_alive:
                return False
            if self.idle_unload_s > 0 and (time.monotonic() - self._last_use) >= self.idle_unload_s:
                self._kill()
                return True
            return False

    def shutdown(self) -> None:
        with self._lock:
            self._kill()


# ======================================================================
#  stdin 桥(持久服务模式, 供 demo 子进程与 HTTP 的推理子进程复用)
# ======================================================================
def serve(argv: list[str] | None = None) -> int:
    """stdin 读 NDJSON 请求, stdout 写 JSON 响应.

    每个输入文件一行请求:  {"id": <int>, "path": "<文件路径>"}
    响应一行:             {"id": <int>, "ok": true, "pages": [...]} 或 {"id":..., "ok":false, "error":...}
    退出:                 {"shutdown": true}
    就绪握手:             首行 {"id": 0, "ready": true}

    可选参数 --det-limit-side-len <int>: 透传 PaddleOCR 的 text_det_limit_side_len
    (检测阶段输入长边上限, 默认 960); HTTP 服务的推理子进程由 _WorkerEngine 传进来.
    """
    device = "auto"
    engine = "onnxruntime"
    det_limit: int | None = None

    # 统一 stdin/stdout 为 UTF-8(Windows 默认 gbk 无法编码中文/¥ 等), 保证 JSON 桥可靠.
    for _s in (sys.stdin, sys.stdout):
        with contextlib.suppress(Exception):
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    if argv:
        it = iter(argv)
        for a in it:
            if a == "--device":
                with contextlib.suppress(StopIteration):
                    device = next(it)
            elif a == "--engine":
                with contextlib.suppress(StopIteration):
                    engine = next(it)
            elif a == "--det-limit-side-len":
                with contextlib.suppress(StopIteration, ValueError):
                    det_limit = int(next(it))
    try:
        engine_obj = ocr(device=device, engine=engine,
                         **({"text_det_limit_side_len": det_limit}
                            if det_limit is not None else {}))
    except Exception as e:  # noqa: BLE001
        print(f"[serve] 引擎初始化失败: {e}", file=sys.stderr)
        return 1

    sys.stdout.write(json.dumps({"id": 0, "ready": True}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("shutdown"):
            break
        req_id = int(req.get("id", 0))
        path = str(req.get("path", "") or "")
        if not path:
            sys.stdout.write(json.dumps({"id": req_id, "ok": False, "error": "empty path"}) + "\n")
            sys.stdout.flush()
            continue
        try:
            pages = engine_obj.process(path)
            sys.stdout.write(
                json.dumps({"id": req_id, "ok": True, "pages": pages}, ensure_ascii=False) + "\n"
            )
        except Exception as e:  # noqa: BLE001
            sys.stdout.write(
                json.dumps({"id": req_id, "ok": False, "error": str(e)}, ensure_ascii=False) + "\n"
            )
        sys.stdout.flush()
    return 0


def _idle_unload_loop(worker: _WorkerEngine, stop_event: threading.Event) -> None:
    """后台监控: 周期检查, 空闲超时则杀掉推理子进程."""
    while not stop_event.wait(_IDLE_CHECK_INTERVAL):
        worker.unload_if_idle()


def _process_locked(engine: _WorkerEngine, path: str) -> list[dict[str, Any]]:
    """在全局锁内经子进程执行一次推理(串行避免子进程忙时并发读)."""
    with _OCR_LOCK:
        return engine.process(path)


# ======================================================================
#  运行计数(/health): 让「客户端说失败、服务端日志空白」可分辨
# ======================================================================
class _RequestStats:
    """HTTP /ocr 的运行计数, 由 /health 输出.

    用途: 下次「客户端说请求失败、服务端日志却什么都没有」时, 先看这里——
    requests_total 没涨 = 请求根本没到本服务(客户端/网络/端口问题);
    涨了但 requests_failed 也涨 = 请求到了, 但连接被重置/处理异常.

    - requests_total: 到达 /ocr 的请求数. 在请求体解析之前计数, 所以客户端
      上传到一半就断开也算一次; /health、/docs 等其它路径不计.
    - requests_failed: 请求级失败数(处理中抛异常、客户端断开、响应 5xx).
      单个文件识别失败(整批仍 200 且该项 ok:false)不算, 那有单独的 error 日志.
    - last_request_ts: 最近一次 /ocr 请求到达时间, ISO 8601 UTC 字符串
      (如 "2026-10-26T01:31:11.482+00:00"); 从未收到过为 null.
    - last_failure_ts / last_failure_reason: 最近一次请求级失败的时间与原因(便于定位).
    全程加锁; 每请求一次加锁, 相对秒级 OCR 耗时开销可忽略.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._failed = 0
        self._last_ts: str | None = None
        self._last_failure_ts: str | None = None
        self._last_failure_reason: str | None = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds")

    def record_request(self) -> None:
        with self._lock:
            self._total += 1
            self._last_ts = self._now_iso()

    def record_failure(self, reason: str) -> None:
        with self._lock:
            self._failed += 1
            self._last_failure_ts = self._now_iso()
            self._last_failure_reason = reason

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_total": self._total,
                "requests_failed": self._failed,
                "last_request_ts": self._last_ts,
                "last_failure_ts": self._last_failure_ts,
                "last_failure_reason": self._last_failure_reason,
            }


# 客户端断开/连接重置时可能冒出来的异常类名(Starlette 抛 ClientDisconnect, 其余是 OS 层)
_CLIENT_GONE_ERRORS = (
    "ClientDisconnect",
    "RemoteDisconnected",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
)


def _install_request_stats(app: Any, stats: _RequestStats) -> None:
    """给 /ocr 挂「请求计数 + 客户端断开告警」中间件(见 _RequestStats 说明).

    中间件比路由早执行: 请求体还没解析就先 requests_total +1, 所以客户端在
    上传途中断开(这种请求原本服务端日志一行都不留)也会留下计数和一条 warning.
    """

    @app.middleware("http")
    async def _request_stats_middleware(request: Any, call_next: Any) -> Any:
        if request.url.path != "/ocr":
            return await call_next(request)
        stats.record_request()
        try:
            response = await call_next(request)
        except asyncio.CancelledError:
            stats.record_failure("请求被取消(客户端断开或服务关闭)")
            _LOG.warning("客户端在 /ocr 处理中断开连接(task cancelled): client=%s", request.client)
            raise
        except Exception as e:  # noqa: BLE001 - 记录后原样抛出, 不改变错误语义
            reason = f"{type(e).__name__}: {e}"
            stats.record_failure(reason)
            if type(e).__name__ in _CLIENT_GONE_ERRORS:
                _LOG.warning(
                    "客户端在 /ocr 请求中断开连接(请求体可能未收全): %s client=%s",
                    reason,
                    request.client,
                )
            else:
                _LOG.exception("/ocr 请求异常中断: %s client=%s", reason, request.client)
            raise
        if response.status_code >= 500:
            stats.record_failure(f"HTTP {response.status_code}")
            _LOG.error("/ocr 返回 HTTP %d", response.status_code)
        return response


def build_app(
    worker: Any,
    title: str = "PP-OCRv6 OCR Service",
    description: str = "PP-OCRv6 本机 HTTP 公共服务. 上传图片/PDF 识别, 完整接口说明见仓库 API.md.",
    version: str = "2.0.0",
) -> Any:
    """构建 FastAPI 应用(供 serve_http 与自动化测试复用).

    worker 需暴露 device/engine/model_loaded/process(path)/shutdown().
    """
    from fastapi import FastAPI, File, UploadFile

    # Pydantic/FastAPI 在模块全局命名空间解析路由函数注解; fastapi 惰性导入,
    # 把注解引用的名字注入模块全局供其解析.
    globals().update(UploadFile=UploadFile, File=File)

    app = FastAPI(title=title, description=description, version=version)
    stats = _RequestStats()
    _install_request_stats(app, stats)

    @app.get("/health")
    def health() -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": True,
            "device": worker.device,
            "engine": worker.engine,
            "model_loaded": worker.model_loaded,
        }
        body.update(stats.snapshot())
        return body

    @app.post("/ocr")
    async def ocr_endpoint(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        _LOG.debug("/ocr 请求进入: 文件数=%d", len(files))
        for f in files:
            name = f.filename or "upload"
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_EXTS:
                _LOG.warning("扩展名不支持: %s (%s)", name, suffix or "(none)")
                results.append(
                    {
                        "filename": name,
                        "ok": False,
                        "error": f"unsupported extension: {suffix or '(none)'}",
                    }
                )
                continue
            tmp_path = ""
            start = time.perf_counter()
            try:
                data = await f.read()
                _LOG.debug("/ocr 文件 %s: %d 字节 (扩展名 %s)", name, len(data), suffix or "(none)")
                # delete=False: 文件在 with 退出后仍保留, 供 worker 推断; 之后 finally 清理
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                pages = await asyncio.to_thread(_process_locked, worker, tmp_path)
                results.append({"filename": name, "ok": True, "pages": pages})
                _LOG.info(
                    "识别 OK: %s pages=%d lines=%d 用时=%.2fs",
                    name,
                    len(pages),
                    sum(len(p["texts"]) for p in pages),
                    time.perf_counter() - start,
                )
            except Exception as e:  # noqa: BLE001
                results.append({"filename": name, "ok": False, "error": str(e)})
                _LOG.error("识别失败: %s 错误=%s", name, e, exc_info=True)
            finally:
                if tmp_path:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
        return {"results": results}

    return app


def serve_http(argv: list[str] | None = None) -> int:
    """本机 HTTP 公共服务: FastAPI + uvicorn, 默认绑定 127.0.0.1.

    端点:
      GET  /health -> {"ok": true, "device": "cpu"|"gpu:0", "engine": ..., "model_loaded": bool}
      POST /ocr  (multipart, 字段 files) -> {"results": [ {filename, ok, pages|error}, ... ]}
    附带的 FastAPI 内置文档: GET /docs, /redoc, /openapi.json

    推理用子进程隔离: 父进程仅 fastapi/uvicorn(~50MB); 空闲超过 idle_unload_s 秒杀掉
    推理子进程, 模型内存全部归还系统. 首请求冷启动子进程约 3~5s.
    配置来自 config.json (--config 指定), CLI 参数优先.
    """
    if argv is None:
        argv = sys.argv[1:]

    # 先用默认解析拿到 --config, 再以配置文件补齐默认值后二次解析(CLI 参数优先)
    cfg = _load_config(_resolve_config_path(_DEFAULT_CONFIG_PATH))
    pre = argparse.ArgumentParser(prog="ocr_service http", add_help=False)
    pre.add_argument("--config", default=_DEFAULT_CONFIG_PATH)
    pre_args, _ = pre.parse_known_args(argv)
    cfg = _load_config(_resolve_config_path(pre_args.config))

    parser = argparse.ArgumentParser(prog="ocr_service http")
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG_PATH,
        help="配置文件路径(JSON), 默认 config.json; CLI 参数优先于配置",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址, 默认 127.0.0.1 (本机)")
    parser.add_argument("--port", type=int, default=18010, help="监听端口, 默认 18010 (5 位数)")
    parser.add_argument(
        "--engine",
        default="onnxruntime",
        help="推理引擎: onnxruntime(默认, 免 paddle/CUDA, 通用) / paddle(GPU 更快)",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="推理设备: auto(onnxruntime 默认 cpu; paddle 有 CUDA 则 gpu:0, 否则 cpu) / cpu / gpu:0",
    )
    parser.add_argument(
        "--idle-unload",
        type=float,
        default=60.0,
        help="空闲多少秒后杀掉推理子进程(子进程隔离, 省内存); 0=不杀, 默认 60",
    )
    parser.add_argument(
        "--det-limit-side-len",
        type=int,
        default=None,
        help=("文本检测输入长边上限(默认 960, 即 PaddleOCR 默认). 票据小字/手写认不全时可调大"
              "(如 1280/1600): 检出更稳, 但 CPU 单页耗时随像素数上升. 也可写进 config.json "
              "的 det_limit_side_len"),
    )
    parser.add_argument(
        "--timeout-keep-alive",
        type=float,
        default=float(TIMEOUT_KEEP_ALIVE_DEFAULT),
        help=(
            "keep-alive 连接空闲超时(秒), 默认 120. uvicorn 自己的默认是 5s: 空闲超过 5s "
            "服务端就发 FIN 关连接, 调用方复用该连接时可能撞上已关闭的连接(10054 RST). "
            "必须是正数; 也可写进 config.json 的 timeout_keep_alive"
        ),
    )
    # config.json 里的 timeout_keep_alive 先校验(CLI 覆盖前), 避免非法默认值被 argparse
    # 的 type 转换直接 SystemExit, 而是退回默认值并告警.
    if "timeout_keep_alive" in cfg:
        cfg["timeout_keep_alive"] = _resolve_timeout_keep_alive(cfg["timeout_keep_alive"])
    parser.set_defaults(**{k: cfg[k] for k in _CFG_KEYS if k in cfg})
    args = parser.parse_args(argv)
    # CLI 侧同样校验(0/负数/NaN 会让 uvicorn 行为不可预期), 非法退回默认
    timeout_keep_alive = _resolve_timeout_keep_alive(args.timeout_keep_alive)

    # 配置日志 (文件轮转 + 有真实控制台时输出), 级别来自 config.json log_level
    _setup_logging(level=cfg.get("log_level", "info"))
    _LOG.info(
        "启动 HTTP 服务: host=%s port=%s engine=%s device=%s idle_unload=%ss det_limit=%s "
        "timeout_keep_alive=%ss",
        args.host,
        args.port,
        args.engine,
        args.device,
        args.idle_unload,
        args.det_limit_side_len or f"默认({DET_LIMIT_SIDE_LEN_DEFAULT})",
        timeout_keep_alive,
    )

    # 惰性导入(仅 uvicorn): 保证 `import ocr_service` 无需 fastapi
    import uvicorn

    worker = _WorkerEngine(engine=args.engine, device=args.device,
                           idle_unload_s=args.idle_unload,
                           det_limit_side_len=args.det_limit_side_len)

    stop_event = threading.Event()
    if args.idle_unload > 0:
        threading.Thread(target=_idle_unload_loop, args=(worker, stop_event), daemon=True).start()

    app = build_app(worker)

    # 启动时卸载(首请求)前的空闲不计入监听场景; 仅关闭时清理子进程
    try:
        # log_config=None: 让 uvicorn 的访问/错误日志走根日志器(即 logs/ocr_service.log)
        # timeout_keep_alive: 不传则 uvicorn 用默认 5s, 空闲复用连接会被服务端关闭(10054 RST)
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_config=None,
            # uvicorn 的注解是 int 秒(运行时交给 asyncio.wait_for, float 也接受)
            timeout_keep_alive=timeout_keep_alive,  # type: ignore[arg-type]
        )
    finally:
        stop_event.set()
        worker.shutdown()
    return 0


def _add_common_http_args(parser: argparse.ArgumentParser) -> None:
    """为 CLI 非 http/serve 模式补充引擎/设备参数(保持入口一致)."""
    parser.add_argument(
        "--engine",
        default="onnxruntime",
        help="推理引擎: onnxruntime(默认, 免 paddle/CUDA) / paddle(GPU 更快)",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="推理设备: auto(onnxruntime 默认 cpu; paddle 有 CUDA 则 gpu:0, 否则 cpu) / cpu / gpu:0",
    )
    parser.add_argument(
        "--det-limit-side-len",
        type=int,
        default=None,
        help="文本检测输入长边上限(默认 960). 票据小字/手写认不全时可调大(如 1280/1600)",
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # 持久服务模式 (stdin 桥, 供 demo 与 HTTP 推理子进程)
    if argv and argv[0] == "serve":
        return serve(argv[1:])
    # 本机 HTTP 公共服务模式 (子进程隔离)
    if argv and argv[0] == "http":
        return serve_http(argv[1:])

    parser = argparse.ArgumentParser(description="PP-OCRv6 通用 OCR 识别")
    parser.add_argument("inputs", nargs="+", help="待识别的图片/PDF 路径")
    _add_common_http_args(parser)
    parser.add_argument(
        "--json-output", action="store_true", help="将结果 JSON 写到 <输入名>.ocr.json"
    )
    parser.add_argument(
        "--no-doc-orientation",
        action="store_true",
        help="关闭文档方向分类 (默认开启, 适配旋转票据)",
    )
    parser.add_argument(
        "--no-textline-orientation",
        action="store_true",
        help="关闭文本方向校正, 稍快但可能漏翻转文本",
    )
    parser.add_argument(
        "--enable-mkldnn",
        action="store_true",
        help="开启 CPU MKLDNN 加速 (Paddle 引擎; 部分模型版本可能报错, 默认关闭)",
    )
    args = parser.parse_args(argv)

    engine = ocr(
        device=args.device,
        engine=args.engine,
        enable_mkldnn=args.enable_mkldnn,
        use_doc_orientation_classify=not args.no_doc_orientation,
        use_textline_orientation=not args.no_textline_orientation,
        **({"text_det_limit_side_len": args.det_limit_side_len}
           if args.det_limit_side_len is not None else {}),
    )

    total_lines = 0
    for path in args.inputs:
        print(f"\n===== {path} =====")
        try:
            pages = engine.process(path)
        except Exception as e:  # noqa: BLE001
            print(f"  [识别失败] {e}")
            continue
        for page in pages:
            print(f"  -- 第 {page['page_index']} 页, 共 {len(page['texts'])} 行 --")
            for line in page["lines"]:
                print(f"    [{line['score']:.2f}] {line['text']}")
            total_lines += len(page["texts"])
        if args.json_output:
            out = Path(path).with_suffix(".ocr.json")
            out.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [已写出] {out}")

    print(f"\n总计识别 {total_lines} 行文本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
