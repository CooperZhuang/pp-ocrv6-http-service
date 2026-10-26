# PP-OCRv6 本机 HTTP OCR 公共服务 — 调用方接口文档

本服务把 PP-OCRv6 识别能力暴露为**仅监听本机（127.0.0.1）**的 HTTP 接口，
供同一台机器上的其他程序上传图片/PDF 文件字节即可识别。支持单文件与多文件列表。

- 默认地址：`http://127.0.0.1:18010`
- 技术栈：FastAPI + uvicorn（用仓库既有约定）
- 默认引擎：**ONNX Runtime**（`onnxruntime`），无需 paddlepaddle / CUDA，任何 Windows 机器可跑
- 推理：（子进程隔离）父进程常驻 ~52MB，模型在子进程，空闲 `idle_unload_s` 秒杀掉子进程释放内存
- 仅监听本机：默认 `--host 127.0.0.1`，不对外暴露

## 启动命令

在 `PP-OCRv6` 目录（或任意目录，python 用绝对路径）执行：

```bash
.venv\Scripts\python.exe ocr_service.py http
```

启动时读取 `config.json`（CLI 参数优先）。也可显式指定：

```bash
.venv\Scripts\python.exe ocr_service.py http --port 18010 --engine onnxruntime --device auto --idle-unload 60
# keep-alive 空闲超时（默认 120s；不要退回 uvicorn 的 5s，见「keep-alive 与连接超时」）：
.venv\Scripts\python.exe ocr_service.py http --timeout-keep-alive 120
# 票据小字（手写金额/编号）认不全时，可提高文本检测分辨率：
.venv\Scripts\python.exe ocr_service.py http --det-limit-side-len 1600
```

## 配置 (config.json)

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1` | 监听地址（仅本机） |
| `port` | `18010` | 监听端口（5 位数；被占用时改之） |
| `engine` | `onnxruntime` | 推理引擎：`onnxruntime`（免GPU）` `paddle`（GPU更快，需装 paddle） |
| `device` | `auto` | 推理设备：`auto`/`cpu`/`gpu:0` |
| `idle_unload_s` | `60` | 空闲多少秒后杀掉推理子进程；`0`=不杀 |
| `timeout_keep_alive` | `120` | keep-alive 连接空闲超时（秒，正数；非法值退回 120）。**不用 uvicorn 默认的 5s**，原因见「keep-alive 与连接超时」 |
| `det_limit_side_len` | `960` | 文本检测输入长边上限（PaddleOCR 的 `text_det_limit_side_len`）。整页送识别时检测阶段会先把长边缩到该值，**票据上的小字/手写会因此变糊**；调到 `1600` 可改善检出，代价是单页 CPU 耗时上升（见 README「识别精度与耗时调参」实测表）。**本仓库 `config.json` 现为 `1600`** |
| `log_level` | `info` | 日志级别：`debug`/`info`/`warning`/`error` |

服务日志写到 `logs\ocr_service.log`（按大小轮转），记录启动、推理子进程启停、每次识别结果（ok/页数/行数/耗时）与错误；无窗口后台运行也落盘。

```json
{
  "host": "127.0.0.1",
  "port": 18010,
  "engine": "onnxruntime",
  "device": "auto",
  "idle_unload_s": 60,
  "timeout_keep_alive": 120,
  "log_level": "info"
}
```

## 引擎与设备说明

### engine

- `onnxruntime`（默认）：**无需 paddlepaddle / CUDA**，任何 Windows 机器可用；CPU 单张约 1.9s。
- `paddle`：可选，需另装 `paddlepaddle`(CPU) 或 `paddlepaddle-gpu`(GPU)；GPU 时单张约 0.64s。

### device

`--device auto`（默认）时按引擎判定实际设备：

- `onnxruntime` 引擎 → 固定 `cpu`（通用部署）。
- `paddle` 引擎 → 有可用 CUDA 则 `gpu:0`，否则退回 `cpu`。

### 子进程隔离（内存行为）

推理在**子进程**里运行，父进程（HTTP 服务）实测仅 ~51MB：

- 启动/空闲：`model_loaded=false`，无子进程。
- 首次 `/ocr`：启动子进程加载模型（常驻 ~0.9GB / 提交 ~1.6GB，冷启动实测 2.4s），`model_loaded=true`。
- 空闲超过 `idle_unload_s` 秒：杀掉子进程，模型内存全部归还系统，`model_loaded=false`。
- 下次 `/ocr`：重新冷启动子进程（实测 2.4s）。

`GET /health` 的 `model_loaded` 反映当前是否已有推理子进程（模型）在运行。

## 性能与并发

- **耗时口径**：开发机（Ryzen 7 9700X）实测 900×600 小图 **0.33s**、A4 300dpi 整页
  （2480×3508）**2.59s**。文本行数越多 rec 越慢，请按自己的票据实测。
- **严格串行**：同一请求里的多个 `files` 按顺序逐个识别；服务端还有全局锁，多个并发请求
  也只会排队执行。**并发上传不会更快**，吞吐 ≈ 1 / 单张耗时。
- **单核瓶颈**：`OMP_NUM_THREADS` 从 1 调到 16 几乎不改变耗时，OCR 只占约 1 个核。
- **批量建议**：单个请求控制在 10~20 个文件，并把调用方 HTTP 超时设宽
  （A4 整页按 2~10s/页估算，多页 PDF 线性叠加，冷启动另加 2~5s）。
- 首次请求会把推理子进程拉起到常驻 ~0.9GB（提交 ~1.6GB），空闲 `idle_unload_s` 秒后释放，
  所以「空闲后第一个请求」要预留冷启动时间。

## keep-alive 与连接超时

uvicorn 默认 `timeout_keep_alive=5s`：空闲超过 5s 的连接由服务端发 FIN 关闭。
本服务的调用方（`票据AI识别`）每线程复用一条 keep-alive 连接，若客户端在
「服务端已发 FIN、自己尚未处理」的毫秒级窗口里发出 `POST /ocr`，就会撞上已关闭的
连接——Windows 客户端表现为 **10054（连接被重置）**。2026-10-24 00:31:11 生产环境
出现过一次（约占当天请求 0.34%），且服务端日志当时没有任何记录。

因此：

- 服务端默认 `timeout_keep_alive=120`（`config.json` / `--timeout-keep-alive` 可改，
  必须为正数，非法值自动退回 120），远大于调用方的空闲间隔。
- `GET /health` 增加运行计数（见下），配合 `logs\ocr_service.log` 的 warning，
  下次能区分「请求没到服务端」和「到了但连接被重置」。
- 客户端断开/请求异常会在日志里留一条 warning，例如：
  `客户端在 /ocr 请求中断开连接(请求体可能未收全): ClientDisconnect: ...`。

## 就绪探测

```
GET http://127.0.0.1:18010/health
```

- 无请求体。
- 响应 `200`：

```json
{
  "ok": true,
  "device": "cpu",
  "engine": "onnxruntime",
  "model_loaded": false,
  "requests_total": 12,
  "requests_failed": 1,
  "last_request_ts": "2026-10-26T01:31:11.482+00:00",
  "last_failure_ts": null,
  "last_failure_reason": null
}
```

| 字段 | 含义 |
| --- | --- |
| `device` | `"cpu"` 或 `"gpu:0"` |
| `engine` | `"onnxruntime"` 或 `"paddle"` |
| `model_loaded` | 推理子进程（模型）是否已在运行 |
| `requests_total` | 到达 `/ocr` 的请求数（在解析请求体之前计数，客户端上传中断开也算；`/health` 自身不计） |
| `requests_failed` | 请求级失败数：处理中抛异常、客户端断开、响应 5xx。**单个文件识别失败（整批仍 200、该文件 `ok:false`）不计**，那种情况看日志里的 error 行 |
| `last_request_ts` | 最近一次 `/ocr` 请求到达时间，**ISO 8601 UTC**（如 `2026-10-26T01:31:11.482+00:00`）；从未收到过为 `null` |
| `last_failure_ts` | 最近一次请求级失败时间（同上格式），从未失败为 `null` |
| `last_failure_reason` | 最近一次请求级失败原因（如 `ClientDisconnect: ...` / `HTTP 503`），从未失败为 `null` |

字段全是进程内累加值，服务重启归零。用法：客户端报失败而 `requests_total` 没涨
→ 请求没到本服务（客户端/网络/端口问题）；涨了且 `requests_failed` 同时涨
→ 请求到了但连接被重置/处理异常，看 `last_failure_reason` 与日志 warning。

服务进程启动即就绪（父进程），首请求会加载模型。

## 识别

```
POST http://127.0.0.1:18010/ocr
```

### 请求

- `Content-Type: multipart/form-data`
- 字段名：`files`
- **可重复传多个 `files` 字段**；顺序等同于 `results` 顺序；只传一个亦可。
- 每个文件支持的扩展名：`.pdf .ofd .jpg .jpeg .png .bmp .tif .tiff`

### 响应（HTTP 200）

```json
{
  "results": [
    {
      "filename": "a.png",
      "ok": true,
      "pages": [
        {
          "input_path": "...",
          "page_index": 0,
          "texts": ["电子发票", "发票号码：...", "手写备注", ...],
          "scores": [1.0, 1.0, 1.0, ...],
          "boxes": [[x1,y1,x2,y2], ...],
          "polys": [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ...],
          "lines": [
            {"text": "电子发票", "score": 1.0, "box": [[539,178],[788,178],[788,243],[539,243]]},
            ...
          ]
        }
      ]
    },
    {
      "filename": "bad.pdf",
      "ok": false,
      "error": "unsupported extension: .pdfzzz"
    }
  ]
}
```

`pages` 为页面结果列表，多页 PDF 每页一项。单页字段含义与模块用法 `ocr_file()`
完全一致：

| 字段 | 含义 |
| --- | --- |
| `input_path` | 输入路径 |
| `page_index` | 页索引（从 0 起） |
| `texts` | 每行文本，`string[]` |
| `scores` | 每行置信度，`number[]` |
| `boxes` | 每行矩形框 `[[x1,y1,x2,y2],...]`（4 坐标） |
| `polys` | 每行四点多边形 `[[[x1,y1],[x2,y2],[x3,y3],[x4,y4]],...]` |
| `lines` | 每行聚合：`[{text, score, box}, ...]` |

### 错误语义

- 单个文件识别失败、或扩展名不支持 → 该项 `ok:false`（HTTP 仍 `200`，不中断整批）。
- 请求未带 `files` 字段 → FastAPI 返回 `422`。
- 临时文件读写失败 → 该项 `ok:false`。

## curl 示例

单文件：

```bash
curl -s -X POST http://127.0.0.1:18010/ocr -F "files=@C:/票据/a.png"
```

多文件：

```bash
curl -s -X POST http://127.0.0.1:18010/ocr \
  -F "files=@C:/票据/a.png" \
  -F "files=@C:/票据/b.pdf"
```

## 跨机器部署（免 GPU）

默认引擎为 `onnxruntime`，**无需** paddlepaddle / CUDA。目标机需要 Windows x64 + `uv` + 互联网
（uv 会自动下载 CPython 3.11，无需预装 Python）：

```bash
# 1) 一键部署环境: uv → CPython 3.11 venv → 装依赖 → 版本自检
#    ⚠️ 不要直接拷贝源机器的 .venv: 它不可移植, 在目标机会失效
bin\deploy_env.bat
# 2) 一键自检: 依赖 / 模型 / 真实识别 / HTTP 端到端
bin\selftest_ocr.bat
# 3) 一键运行
bin\start_http.bat
```

首次运行（或首次自检）自动下载 ONNX 模型（联网一次，约 146MB）；之后可离线复用。
哪些目录不必拷贝、内网离线怎么做，见仓库 `README.md` 的「部署到其他设备」小节。
开机自启见仓库 `bin\install_scheduled_task.bat`（**换机器或换目录后必须重跑**，任务里是绝对路径）。

## 交互式文档（FastAPI 内置）

- `GET /docs` — Swagger UI（可直接试调）
- `GET /redoc` — ReDoc
- `GET /openapi.json` — OpenAPI 规范 JSON
