# PP-OCRv6 HTTP OCR Service

基于 **PaddleOCR 3.7.0 + PP-OCRv6_medium** 的通用 OCR 服务，通过 HTTP 接口上传图片/PDF
即可识别，支持单文件与多文件列表。

- 默认引擎 **ONNX Runtime**：**无需 paddlepaddle / CUDA**，任何 Windows 机器可跑（CPU 单张约 1.9s）。
- 可选 **Paddle** 引擎配合 GPU（单张约 0.64s）。
- HTTP 服务推理走 **[子进程隔离]**：父进程常驻约 50MB，模型放进子进程，空闲自动杀掉子进程，
  模型内存全部归还系统——极省内存（适合 8GB 机器）。
- 提供结构化的配置（`config.json`）、结构化日志（轮转）、无窗口后台运行、任务计划自启脚本。

## 功能

- HTTP 上传识别：`POST /ocr`（multipart，字段 `files`，单/多文件），`GET /health` 就绪探测。
- 模型懒加载 + 空闲自动卸载（子进程隔离）。
- 引擎/设备自适应：ONNX（免 GPU）或 Paddle（GPU 加速）。
- 配置化（`config.json`）+ 结构化日志 + 无窗口后台 + 开机自启。
- 自动化测试（单元 + 真实模型冒烟）、lint/format/type check、CI。

## 目录结构

```
.
├── ocr_service.py            # 核心模块 + CLI + HTTP 服务
├── config.json               # 服务配置 (host/port/engine/device/idle_unload_s/log_level)
├── requirements.txt          # 运行依赖
├── requirements-dev.txt      # 开发/测试依赖
├── prepare_env.bat           # 环境准备脚本 (优先 uv, 回退 pip)
├── pyproject.toml            # 项目元数据 + pytest/mypy/coverage/ruff 配置
├── API.md                    # HTTP 服务调用方接口文档
├── models/                   # 模型缓存 (PaddleX, 首次运行自动下载; 已 gitignore)
├── logs/                     # 运行日志 (ocr_service.log, 轮转; 已 gitignore)
├── bin/
│   ├── start_http.bat        # 无窗口启动 HTTP 服务 (读 config.json)
│   ├── stop_service.bat      # 按端口停止服务进程
│   ├── run_hidden.vbs        # 隐藏(无窗口)启动器
│   └── install_scheduled_task.bat  # 注册/卸载任务计划自启
└── tests/                    # 单元测试 + 真实模型冒烟 (@integration)
```

## 配置 (config.json)

服务启动时读取 `config.json`（CLI 参数优先于配置文件）：

```json
{
  "host": "127.0.0.1",
  "port": 18010,
  "engine": "onnxruntime",
  "device": "auto",
  "idle_unload_s": 60,
  "log_level": "info"
}
```

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1` | 监听地址（仅本机） |
| `port` | `18010` | 监听端口 |
| `engine` | `onnxruntime` | 推理引擎：`onnxruntime`（免GPU）/ `paddle`（GPU更快） |
| `device` | `auto` | 设备：`auto`/`cpu`/`gpu:0` |
| `idle_unload_s` | `60` | 空闲多少秒后杀掉推理子进程；`0`=不杀 |
| `log_level` | `info` | 日志级别：`debug`/`info`/`warning`/`error` |

## 安装

```bash
# 一键准备环境 (优先用 uv 装依赖; 无 uv 或 uv 失败自动回退 pip)
prepare_env.bat
```

> 默认引擎是 `onnxruntime`，**不需要** paddlepaddle / CUDA。

首次运行自动下载 ONNX 模型到仓库 `models/` 目录（联网一次，之后离线复用）。

### 可选：GPU 加速（`--engine paddle`）

需装 GPU 版 paddle（Paddle 官方源，cu126，自带 CUDA/cuDNN）：

```bash
uv pip install --python <venv-python> --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ paddlepaddle-gpu==3.3.1
```

装 GPU 版后 `--engine paddle --device auto` 自动用 `gpu:0`；装的是 CPU 版则自动走 CPU。

## 使用

### 命令行识别

```bash
.venv\Scripts\python.exe ocr_service.py <图片或PDF路径>... [选项]
# --engine onnxruntime|paddle  (默认 onnxruntime)
# --device auto|cpu|gpu:0
# --json-output 等
```

### HTTP 服务

```bash
.venv\Scripts\python.exe ocr_service.py http            # 读取 config.json
curl -s http://127.0.0.1:18010/health                   # {"ok":true,"device":"cpu","engine":"onnxruntime","model_loaded":false}
curl -s -X POST http://127.0.0.1:18010/ocr -F "files=@a.png" -F "files=@b.png"
```

完整接口契约见 [`API.md`](API.md)；交互式文档 `GET /docs` / `GET /redoc` / `GET /openapi.json`。

### 作为模块使用

```python
import sys

sys.path.insert(0, r"C:\<repo>\PP-OCRv6")
import ocr_service

engine = ocr_service.ocr()  # 默认 engine='onnxruntime', 懒加载
pages = engine.process("C:/票据/发票.png")
```

## 引擎与设备

| 场景 | 命令 | 单张耗时 |
| --- | --- | --- |
| 任意 Windows 机器（无 GPU） | 默认（`onnxruntime` + `cpu`） | ~1.9s |
| GPU（更快） | `--engine paddle --device gpu:0` | ~0.64s |
| 纯 CPU 备用 | `--engine paddle --device cpu` | ~19.6s |

- ONNX Runtime：无需 paddlepaddle/CUDA，任何 Windows 机器可跑。
- Paddle：GPU 时最快；需装对应 paddle 版本。

## 子进程隔离与懒加载（省内存）

HTTP 服务把推理放进**子进程**，父进程只跑 fastapi/uvicorn（~50MB）。

| 时刻 | 父进程 | 推理子进程 | `model_loaded` |
| --- | --- | --- | --- |
| 启动/空闲 | ~52MB | 不存在 | `false` |
| 首请求 | ~52MB | ~1.5GB（模型） | `true` |
| 空闲 `idle_unload_s` 秒后 | ~52MB | **被杀掉，内存全部归还系统** | `false` |
| 下次请求 | ~52MB | 重新冷启动（约 3~5s） | `true` |

适合「长期空闲、偶尔批量」的场景；首次请求冷启动约 3~5s。

## 开机自启（任务计划程序，无需管理员）

`bin\` 含三套脚本：

| 脚本 | 用途 |
| --- | --- |
| `bin\install_scheduled_task.bat` | 注册「登录时自启」任务（`\PP-OCRv6\PP-OCRv6_HTTP`）；`--remove` 卸载 |
| `bin\start_http.bat` | 无窗口启动 HTTP 服务（读 config.json） |
| `bin\stop_service.bat` | 按端口停止服务进程（默认 18010） |

```bash
bin\install_scheduled_task.bat        # 注册(登录时启动, 无窗口, 无需管理员)
bin\install_scheduled_task.bat --remove   # 卸载
bin\stop_service.bat / bin\start_http.bat # 手动停止/启动
```

日志：`logs\ocr_service.log`（按大小轮转），记录启动、推理子进程启停、每次识别结果与错误。

## 开发 / 测试

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && ruff format --check . && mypy ocr_service.py
pytest                                  # 单元测试 (默认不跑集成)
pytest -m integration                   # 真实模型冒烟 (首次联网下载模型)
```

GitHub Actions CI（`.github/workflows/ci.yml`）：`lint-test`（ruff/format/mypy/单测）与
`integration`（真实 ONNX 冒烟）两道门禁。

## 模型缓存

模型缓存到**仓库目录 `models/`**（`PADDLE_PDX_CACHE_HOME` 指向 `<repo>/models`），
随项目走、不散落在用户主目录；`models/` 与 `logs/` 已被 `.gitignore` 排除，不会提交。
首次运行联网下载一次，之后离线复用。

## License

MIT
