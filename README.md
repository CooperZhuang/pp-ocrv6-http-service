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
├── prepare_env.bat           # 环境准备脚本 (无 uv 时的 pip 回退路径)
├── pyproject.toml            # 项目元数据 + pytest/mypy/coverage/ruff 配置
├── API.md                    # HTTP 服务调用方接口文档
├── models/                   # 模型缓存 (PaddleX, 首次运行自动下载; 已 gitignore)
├── logs/                     # 运行日志 (ocr_service.log, 轮转; 已 gitignore)
├── bin/
│   ├── deploy_env.bat        # ① 一键部署环境 (uv + CPython 3.11 + 依赖 + 自检)
│   ├── selftest_ocr.bat      # ② 一键自检 (环境/模型/真实识别/HTTP 端到端)
│   ├── selftest_ocr.py       #    自检实现, 被 selftest_ocr.bat 调用
│   ├── start_http.bat        # ③ 一键运行: 无窗口启动 HTTP 服务 (读 config.json)
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

## 安装（一键）

```bash
bin\deploy_env.bat             # 推荐: 检查 uv → 建 CPython 3.11 venv → 装依赖 → 自检
bin\deploy_env.bat --recreate  # 删掉 .venv 重建 (依赖装坏时用; 重建后 GPU 版 paddle 需按下文重装)
bin\deploy_env.bat --no-pause  # 供脚本调用时不暂停
```

- 目标机只需要 **uv** + **互联网**：uv 会自动下载 **CPython 3.11**（本项目验证版本），
  **不必预装 Python**。没有 uv 时脚本会直接报错并给出安装命令。
- 无 uv 的老机器可用 `prepare_env.bat`（pip 回退路径，要求系统已有 Python 3.11）。
- 默认引擎是 `onnxruntime`，**不需要** paddlepaddle / CUDA；磁盘约需 venv ~3GB + 模型 ~146MB。

首次运行（或首次自检）自动下载 ONNX 模型到仓库 `models/` 目录（联网一次，之后离线复用）。
完整的跨机器部署流程（哪些目录不用拷、内网离线怎么办）见下文「部署到其他设备」。

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

| 场景 | 命令 | 单张耗时（口径见下） |
| --- | --- | --- |
| 任意 Windows 机器（无 GPU） | 默认（`onnxruntime` + `cpu`） | ~1.9s（A4 300dpi 整页） |
| GPU（更快） | `--engine paddle --device gpu:0` | ~0.64s |
| 纯 CPU 备用 | `--engine paddle --device cpu` | ~19.6s |

- **耗时口径**：上表按 A4 300dpi 整页发票（2480×3508）量级。开发机实测 900×600 小图为
  **0.33s**、2480×3508 整页为 **2.59s**；文本行数越多 rec 越慢，请按自己的票据实测。
- **该负载基本是单核瓶颈**：`OMP_NUM_THREADS` 从 1 调到 16 几乎不改变耗时（整页 2.56s → 2.44s）。
  所以目标机核数多不会更快，4 vCPU 只是按单核性能线性变慢；好处是 OCR 只占约 1 个核，
  不会压垮同机其他服务。
- ONNX Runtime：无需 paddlepaddle/CUDA，任何 Windows 机器可跑。
- Paddle：GPU 时最快；需装对应 paddle 版本。

## 子进程隔离与懒加载（省内存）

HTTP 服务把推理放进**子进程**，父进程只跑 fastapi/uvicorn（实测 51MB）。

| 时刻 | 父进程 | 推理子进程 | `model_loaded` |
| --- | --- | --- | --- |
| 启动/空闲 | ~51MB（实测） | 不存在 | `false` |
| 首请求 | ~51MB | 常驻 ~0.9GB / 提交 ~1.6GB（实测） | `true` |
| 空闲 `idle_unload_s` 秒后 | ~51MB | **被杀掉，内存全部归还系统** | `false` |
| 下次请求 | ~51MB | 重新冷启动（实测 2.4s） | `true` |

适合「长期空闲、偶尔批量」的场景；首次请求冷启动实测 2.4s（开发机，目标机按单核性能更慢）。

## 开机自启（任务计划程序，无需管理员）

`bin\` 下的运维脚本：

| 脚本 | 用途 |
| --- | --- |
| `bin\deploy_env.bat` | 一键部署环境（uv + CPython 3.11 + 依赖 + 自检） |
| `bin\selftest_ocr.bat` | 一键自检：依赖 / 模型 / 真实识别 / HTTP 端到端 |
| `bin\install_scheduled_task.bat` | 注册「登录时自启」任务（`\PP-OCRv6\PP-OCRv6_HTTP`）；`--remove` 卸载 |
| `bin\start_http.bat` | 无窗口启动 HTTP 服务（读 config.json） |
| `bin\stop_service.bat` | 按端口停止服务进程（默认 18010） |

```bash
bin\install_scheduled_task.bat        # 注册(登录时启动, 无窗口, 无需管理员)
bin\install_scheduled_task.bat --remove   # 卸载
bin\stop_service.bat / bin\start_http.bat # 手动停止/启动
```

日志：`logs\ocr_service.log`（按大小轮转），记录启动、推理子进程启停、每次识别结果与错误。

## 部署到其他设备

目标机需要：**Windows x64 + uv + 互联网**。无需 GPU、无需预装 Python。

| 目录/文件 | 是否要拷 | 说明 |
| --- | --- | --- |
| `ocr_service.py` `config.json` `requirements.txt` `pyproject.toml` | **要** | 运行必需 |
| `bin\` | **要** | 三个一键脚本 + 启动/停止/自启脚本 |
| `README.md` `API.md` | 建议 | 交接文档 |
| `tests\`（34KB） | 可选 | 自检默认用 `tests\fixtures\sample_invoice.png`；不拷则用 `bin\selftest_ocr.bat <你的图>` |
| `.venv\` | **不要** | 不可移植：`pyvenv.cfg` 的 `home` 指向本机 uv 托管的 CPython；目标机用 `bin\deploy_env.bat` 重建 |
| `models\` | **不要**（有网） | 首次识别自动下载 ~146MB；内网/离线才拷 `models\official_models\*_onnx`（可省 176MB） |
| `logs\` | **不要** | 运行期日志，目标机自动创建 |
| `__pycache__\` `.pytest_cache\` `.mypy_cache\` `.ruff_cache\` `.coverage` | **不要** | 缓存与产物 |
| `.git\` | 可选 | 目标机若要 `git pull` 更新则带，否则不带 |
| `.github\` `requirements-dev.txt` | **不要** | CI 配置与开发依赖，运行无关 |

```bash
# 1) 按上表拷项目到目标机, 例如 C:\PP-OCRv6
# 2) 一键部署环境
bin\deploy_env.bat
# 3) 一键自检 (首次会下载模型 ~146MB)
bin\selftest_ocr.bat
# 4) 一键运行 + 开机自启
bin\start_http.bat
bin\install_scheduled_task.bat
```

> 换机器或换目录后**必须重跑** `install_scheduled_task.bat`：任务计划里写的是绝对路径。
>
> 网络不好的场景：paddlex 默认从 huggingface 取模型，可改用 `PADDLE_PDX_MODEL_SOURCE=modelscope`
> （或 `bos`）后再启动。

## 验证状态

本轮实测环境：Windows 11 x64 / uv 0.12.1 / CPython 3.11.13 / AMD Ryzen 7 9700X（开发机）。

| 项目 | 状态 |
| --- | --- |
| `bin\deploy_env.bat` 一键部署 | ✅ 真实执行通过（复用/新建 venv、装依赖、版本自检 exit 0） |
| `bin\selftest_ocr.bat` 自检 | ✅ 4/4 PASS，含 HTTP 端到端（临时端口 18011 起的实例） |
| `bin\start_http.bat` / `stop_service.bat` | ✅ 真实启动+停止通过；父进程实测 51MB |
| 真实发票识别（ONNX CPU） | ✅ 900×600 = 0.33s；2480×3508 = 2.59s |
| 推理子进程内存 | ✅ 实测常驻 905MB / 提交 1.63GB |
| 线程扩展性 | ✅ 实测 `OMP_NUM_THREADS` 1→16 无变化（单核瓶颈） |
| 任务计划自启 | ✅ 任务 `\PP-OCRv6\PP-OCRv6_HTTP` 存在、上次登录运行 `LastTaskResult=0`，服务在跑 |
| 单请求多文件 / 并发请求串行 | ⚠️ 仅代码审读：同一请求内文件顺序处理，`_OCR_LOCK` 全局串行 |
| PDF/OFD 多页与旋转票据 | ⚠️ 本轮未实测（依赖 paddlex 渲染） |
| 目标 8GB VM 的绝对耗时 | ⚠️ 未实测：按单核瓶颈推算 A4 整页约 6~9s，落地后用 `bin\selftest_ocr.bat` 现场确认 |
| 无 paddlepaddle 环境跑 ONNX 引擎 | ✅ 屏蔽 `paddle` 模块后真实推理仍通过 |
| `--engine paddle` 的 0.64s / 19.6s | ⚠️ 本轮未复测（只跑了 `onnxruntime` 引擎，GPU 数值沿用原文档） |

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
首次运行联网下载一次，之后离线复用。缓存里 `*_onnx` 目录才是默认引擎需要的 4 个（det/rec/
textline_ori/doc_ori，共约 **146MB**）；同名非 `_onnx` 目录（Paddle 格式）和 `UVDoc_onnx`
只有切 `--engine paddle`、开 `use_doc_unwarping` 时才用，离线拷贝可省掉（清单见「部署到其他设备」）。

## 贡献者须知

改完代码请至少跑一遍 `bin\selftest_ocr.bat`，它覆盖了「真实模型 + HTTP 端到端」这条
最容易出问题、单测又覆盖不到的路径。

## License

MIT
