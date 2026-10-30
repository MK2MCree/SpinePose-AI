# 本机 V2.0.1 步态接入交付说明

记录日期：2026-09-08。本文件描述本轮实施后的状态；此前的 `architecture_review_and_gait_v201_alignment_20260908.md` 是实施前审查记录，不能代替本文件判断功能是否已经接入。

已把独立 ScoNetVideoApp V2.0.1 的真实视频分析接入主程序的登录、病例、任务、复核、Agent 和 PDF 链路。本轮交付源代码和本机运行能力，没有重新构建 EXE、Docker 镜像、发布站点或部署公网服务。主程序原有版本号与步态组件版本分别管理，不把步态 V2.0.1 当成所有组件的统一版本。

## 1. 本机启动

PyCharm 主项目解释器使用：

```text
C:/Users/MK2SG/miniconda3/envs/PythonProject4/python.exe
```

工作目录使用：

```text
C:/Users/MK2SG/PycharmProjects/PythonProject6
```

运行入口：

| 入口 | 用途 | 退出方式 |
| --- | --- | --- |
| `main.py` | 网页服务，浏览器访问 `http://127.0.0.1:8000/` | PyCharm 正常停止或终端 Ctrl+C |
| `launcher.py` | 桌面 WebView 窗口，内部使用动态空闲端口和 loopback 地址 | 关闭桌面窗口 |
| `run_local.ps1 -Mode web` | 显式限定 `127.0.0.1`、单个 Uvicorn worker 的本机网页启动 | Ctrl+C |
| `run_local.ps1 -Mode desktop` | 使用上述解释器启动桌面入口 | 关闭桌面窗口 |

项目目录中的 PowerShell 示例：

```powershell
# 只检查解释器和启动参数，不加载模型或启动应用
.\run_local.ps1 -Mode web -Check

# 本机网页模式
.\run_local.ps1 -Mode web

# 桌面模式
.\run_local.ps1 -Mode desktop
```

脚本不安装依赖，不修改环境配置，不清空或迁移已有病例。它继承当前进程环境；`-PythonPath` 可显式指定另一已配置的解释器，网页模式可用 `-Port` 改端口。直接执行 `main.py` 默认监听 `127.0.0.1:8000`，支持 PyCharm 环境变量 `MINDSPINE_HOST` / `MINDSPINE_PORT`；本轮默认仅供本机访问。

主服务启动会加载既有影像模型。端口占用时先检查是否已有同一程序在运行，不要对同一数据目录同时启动两个实例；步态调度器会拒绝第二个实例占用同一任务目录。

## 2. 两套解释器与只读源工程

主服务继续使用 PythonProject4 环境。步态任务由独立解释器执行，避免把 ScoNet 的依赖直接混入主项目：

```text
默认步态源目录：
C:/Users/MK2SG/Documents/ChatGPT/脊柱步态识别/ScoNetVideoApp

优先使用的步态解释器：
<步态源目录>/.build_gpu_venv/Scripts/python.exe

备用解释器路径：
<步态源目录>/.venv/Scripts/python.exe
```

源目录默认按当前用户的 `Path.home()/Documents/ChatGPT/脊柱步态识别/ScoNetVideoApp` 计算。可通过进程环境显式配置：

| 环境变量 | 含义 |
| --- | --- |
| `MINDSPINE_GAIT_SOURCE_ROOT` | 包含 `sconet_video`、`vendor`、`models` 的源工程目录 |
| `MINDSPINE_GAIT_PYTHON` | 运行步态 worker 的 Python 可执行文件绝对路径 |
| `MINDSPINE_DATA_DIR` | 主程序运行数据根目录，见下一节 |
| `MINDSPINE_FONT_PATH` | PDF 中文字体文件，可选 |

当前本机已验证的独立步态环境为 Python 3.10、PyTorch `2.1.2+cu121`，RTX 4050 Laptop GPU 的 CUDA 兼容检查通过。设备默认 `auto`：由源模块执行兼容检查，不能使用 CUDA 时回退 CPU；显式选择 `cuda` 而兼容检查失败时报告错误。

源工程保持只读。本轮没有把 GB 级模型、build、outputs 或 EXE 复制到主项目，也没有修改源工程。`mindspine/gait_engine_manifest.json` 固定了 75 份源代码/清单的 SHA-256；启动时验证，每个 worker 执行前再次验证。模型本身继续由源模块校验已确定的哈希。后续更新源算法须同时进行版本核对、验收和清单更新，不能仅覆盖外部目录后假定它仍是此次 V2.0.1。

健康入口为登录后的 `GET /api/gait/health`。它表示运行路径、来源清单和调度状态已就绪；实际模型加载和每次输入的质控仍在任务进程中完成。

## 3. 运行配置、数据与 PDF 依赖

主程序在 `main.py` 调用 `mindspine.config.load_settings(APP_ROOT)`；当前配置类名称为 `Settings`。运行目录由 `mindspine.runtime` 计算：源码模式的 `APP_ROOT` 是项目根目录；历史 PyInstaller 入口按 EXE 所在目录计算，但本轮没有验证或重建安装包。

没有设置 `MINDSPINE_DATA_DIR` 时，数据根目录为：

```text
<项目根目录>/temp_uploads
```

该目录保存账号、认证 session、旧个人档案、Agent 病例和任务所需数据库及运行文件。实际位置以启动进程的 `MINDSPINE_DATA_DIR` 为准；不要为了切换入口而随意更换它，否则界面会看到另一个数据目录中的记录。

本轮主程序调用 `GaitService(SETTINGS.data_dir / 'gait')`，服务内部还增加一层 `gait`。为保留已经产生的任务，当前实际步态路径保持为：

```text
<data_dir>/gait/gait/jobs.sqlite3
<data_dir>/gait/gait/<user_id>/<case_id>/<job_id>/input.<扩展名>
<data_dir>/gait/gait/<user_id>/<case_id>/<job_id>/output/
```

这是已知目录布局，不是两套任务存储。未来若简化目录，需要显式迁移；不能直接改构造参数让已有任务消失。步态成果独立于旧影像 session 的 TTL 清理，退出取消任务也不会删除历史报告或归档原片。

当前 `requirements.txt` 已列出 `reportlab==4.5.1`。本机曾遇到主解释器缺少该库，本轮从本机已安装包提供 `.runtime/pdf` 作为运行回退；这是被 `.gitignore` 排除的本机依赖，不会随源码提交。新机器应在对应主解释器中具备声明的依赖，不能假定该回退目录会被分发。启动脚本不会执行安装。中文字体优先使用指定文件或本机可用字体。

网页运行时资源已保存在 `web/vendor`：Tailwind 浏览器运行版固定为 `3.4.17`，Font Awesome 固定为 `6.0.0`，包含原 CSS 声明的全部字体文件、上游许可证及来源/SHA-256 清单。12 个下载文件合计约 1.31 MB，运行时资源已自包含。页面不再向 CDN 或 Google Fonts 请求脚本、样式或字体；正文使用 `Segoe UI / Microsoft YaHei / system-ui`。同步脚本会把 `vendor` 一起复制到 `site/public`。此处保留 CDN 上游 Tailwind 3 浏览器编译器和既有渲染方式，没有升级到 Tailwind 4，也没有完成生产静态 CSS 构建；上游浏览器运行版的开发提示仍可能出现在控制台。外部知识来源链接只在用户点击打开时联网，显式启用的外部 AI 请求仍需要网络。浏览器复验中，影像工作台布局、图标和步态结果展示正常，脚本与样式引用均来自本地 `vendor` 路径。

本机入口读取进程环境变量，**不会自动加载 `.env` 文件**。PyCharm 应通过 Run Configuration 的 Environment variables 提供配置；不要仅复制 `.env.example` 后认为它已经生效。该示例中的 `/data` 是容器风格路径，不应直接当成本机 Windows 数据路径。本轮没有修改用户的 `.env` 或密钥配置。

## 4. 已接通的工作流与接口

工作流为：选择/新建本次病例 → 上传视频 → 自动选人和质控 → 独立推理 → 查看原片采样预览、叠加视频和 Mask → 必要时选择其他轨迹重跑 → 复核备注 → 归档并采用结果 → Agent 引用与综合病例 PDF。

| 接口 | 行为 |
| --- | --- |
| `POST /api/gait/jobs` | multipart `file`、`case_id`、`options_json`，返回真实任务 |
| `GET /api/gait/jobs?case_id=...` | 当前用户在对应病例中的任务记录 |
| `GET /api/gait/jobs/{id}` | 状态、阶段、进度、复核信息 |
| `DELETE /api/gait/jobs/{id}` | 取消未完成任务；不会删除历史资产 |
| `GET /api/gait/jobs/{id}/report` | V2.0.1 结构化报告及声明的工件 URL |
| `GET /api/gait/jobs/{id}/files/{filename}` | 只提供允许且在报告中声明的文件，支持视频 Range |
| `POST /api/gait/jobs/{id}/rerun` | 复用原片，新建结果，不覆盖上一轮 |
| `POST /api/gait/jobs/{id}/review` | 保存备注；显式 `archive:true` 才归档并采用 |
| `GET /api/cases/{case_id}/report.pdf` | 固定模板综合病例报告，读取该病例已采用的步态结果 |

选项支持 `start_seconds`、`end_seconds`、`target_track_id`、`research_demo`、`device`。单段分析上限 45 秒，默认单文件上传上限 512 MiB。有效格式为 MP4、MOV、M4V、AVI、MKV、WebM，能否处理仍由实际解码和质控决定。

当前队列全局最多 10 个在途任务，同用户最多 2 个；接收文件、排队和运行均计入配额。SQLite 原子占位和单个调度 worker 保证步态任务串行执行；此约束不等于主程序所有其他影像操作都统一到同一 GPU 队列。

每个视频使用新进程，避免跟踪状态串扰。Windows Job Object 在放行 worker 前建立，取消、超时、退出会终止该任务及其 ffmpeg 子孙，不会按进程名称清理用户的其他程序。正常退出通过 Uvicorn shutdown 关闭服务；桌面关窗、GUI 初始化失败和启动失败均有清理路径。退出账号仅取消该用户未完成的步态任务，其他用户和已完成记录保留。

任务状态包括 `uploading/queued/running/completed/rejected/failed/cancelled`。异常中断后重新启动，会把遗留未完成任务标为中断失败并允许复用原片重跑；当前没有断帧续算。默认任务超时为 1800 秒。反复确认同一个 Agent 动作使用持久化幂等键，避免重复提交。

报告包装强制执行 `prepare_report_view`，提供 `report_v2.json`、`report.html`、`report_v2.zip`。原始 `report.json` 保持原字节，报告视图记录其 SHA-256。结果文件使用白名单与用户/病例归属检查，不暴露任意文件路径。

同一病例可以保留多次重跑，只有明确归档采用的结果成为当前选定结果；仅修改备注不会撤销或抢占采用状态。Agent 默认读取选定报告，也能明确查询最新任务和有界历史；这些是记录检索，不自动推断不同检查、协议和模型之间的可比性。

## 5. Agent 与知识资料的实际能力

当前 `ClinicalAgent` 结合本次问卷、授权的结构化影像事实、真实步态任务/报告、历史记录与本地来源摘要组织回答。执行动作由应用确认和调度，不由外部文字模型直接修改任务。否定重跑、重复确认、跨病例结果、Provider 故障、同意状态与已采用结果均有专门测试。

问卷采用新的 `consent_confirmed` 字段，同时兼容历史 `adult_confirmed`；显式新字段为 false 时，旧字段不能覆盖它。新病例联网选项默认开启来自本轮用户明确的产品选择，仍可按病例关闭。关闭后不会调用外部模型。外部上下文保留可用的医学答案和当前已授权内容，去掉数据库标识及任意无关字段；原视频不会作为文本 API 附件发送。

资料库目前有 **55 条可追溯来源摘要**，采用中文字符片段、有限别名、BM25 和人群/受众/用途过滤。它不是 55 篇全文索引，也不是已经完成向量检索、重排和独立临床评测的完整 RAG。检索分数仅表示相关性。一般知识、病例事实和工具结果分别保留来源，不能把来源中的示例数值写成患者测量，不能把 X 光/步态置信度平均为总风险。

现有本地导入 CLI：

```powershell
& 'C:\Users\MK2SG\miniconda3\envs\PythonProject4\python.exe' -m mindspine.knowledge stats
& 'C:\Users\MK2SG\miniconda3\envs\PythonProject4\python.exe' -m mindspine.knowledge validate --path reviewed_sources.jsonl
& 'C:\Users\MK2SG\miniconda3\envs\PythonProject4\python.exe' -m mindspine.knowledge import --path reviewed_sources.jsonl
# 明确写入通过校验的来源摘要时才添加 --apply
```

导入默认只读预览；实际增量写入具备锁、校验和原子替换。导入后需重建索引或重启服务，不会自动热加载。格式和来源边界见 `rag_import_v1.md`、`rag_implementation_v1.md`。

DeepSeek 已通过真实应用接口自检：使用既有配置，向官方接口发送仅含公开资料的问题，获得 HTTP 200，回复通过来源 ID 校验，应用审计记录 `external_model_used=true`。验收时修复了提供方返回 `[citation:id]` 与应用 `[id]` 格式不一致的问题；只有资料库中实际存在的 ID 才允许归一化。原有关闭联网与故障回退测试保留。离线知识检索、任务、结果和 PDF 不依赖该请求成功。

## 6. 本轮验证记录

以下真实端到端项目来自本轮主流程执行记录；合成软件测试与真实样例分开列示：

| 项目 | 已观察结果 | 能说明什么 |
| --- | --- | --- |
| 公开 OpenGait 样例真实推理 | 有效帧 118/118 | 该公开样例已走通集成后的真实链路 |
| 与同源基准 Mask 比较 | Mask 完全相同 | 此样例的轮廓产物一致 |
| 同源三类分数比较 | 最大绝对差为 0 | 此样例分数未因本次接入改变 |
| 视频文件 Range | HTTP 206 | 浏览器可按字节范围读取声明的视频工件 |
| 复核/报告/Agent | 已验证归档结果、PDF 和 Agent 引用真实结果 | 同一病例的业务链路已接通 |
| 真实联网 Agent | 官方接口 HTTP 200，应用回复与引用校验通过 | 本机既有 DeepSeek 配置能够用于新的全局 Agent |
| 新旧病例关联 | DR 同图更新不重复，新检查不会混入另一检查资料 | 同账号检查隔离和旧数据兼容均经 HTTP 接口验证 |
| 项目自动测试 | 主流程已记录 89 项通过 | 覆盖队列、病例、Agent、知识检索和生命周期等当前软件行为 |
| 专项生命周期测试 | 真实 Uvicorn + 合成 ASGI + 假窗口 | 关窗、GUI 失败、启动失败均进入关闭/清理路径，没有启动实际 GUI/GPU |
| 子进程取消测试 | 合成 worker 真实派生子进程，取消后停止更新 | 针对该测试进程树的终止机制有效 |

这些验证不提供临床准确率、所有视频的等价保证、所有 GPU 的支持承诺或人群外推结论。ScoNet 输出仍是三类研究模型分数，不是个体患病概率；当前链路没有输出 Cobb 角、三维脊柱、关节角度、步态周期或左右侧运动学曲线。

测试环境说明：主 PyCharm 环境用于应用。全测曾使用本机 Miniconda 基础环境，并仅通过项目 `temp_uploads/test_python_deps` 补入本机已有的纯 Python `python_multipart` 包；没有跨 Python 版本导入整套二进制 site-packages。该目录不随项目提交。独立步态环境也完成过专属测试。不要把某个解释器缺包误认为某项测试已通过或应该跳过。

## 7. 后续路线与难度

| 难度 | 后续工作 | 边界与验收重点 |
| --- | --- | --- |
| 较低 | 整理启动配置、依赖锁定、恢复文档与定期备份演练 | 用新目录恢复一份测试数据，验证账号、病例、归档报告和模型来源可追溯 |
| 中等 | 统一影像/步态/报告的任务接口与资产生命周期 | 减少旧内存影像状态，定义删除、保留期、取消和重启语义，避免跨病例混用 |
| 中等 | 更多采集协议、拍摄引导及独立样例集 | 对多人、遮挡、服装、视角、时长和低光分别记录失败原因，不能仅扩充漂亮演示片 |
| 中高 | 知识库评测、可选向量/混合检索与重排 | 先建立有来源的独立问题集，再比较召回、适用性、引用支持和无答案拒绝 |
| 中高 | 服务器版本 | API、持久队列、GPU worker 和资产存储分工，补齐多进程部署、租户权限、备份恢复及吞吐验收 |
| 中高 | 树莓派/边缘采集端 | 优先负责摄像头采集、质量提示、缓存/上传和结果显示；GPU 服务器负责当前模型推理，不能默认整套 PyTorch/CUDA 链路可直接移到树莓派 |
| 高 | 树莓派本地推理 | 需对目标硬件重新选择/转换/量化模型并实测精度、延迟、温度、内存与供电，不属于本轮已实现内容 |
| 高，需新增模型与验证 | 三维、关键点与生物力学指标 | 先确定单目/多目、同步/标定、坐标系和目标真值；新增姿态/三维重建模型及独立误差验证，不能从现有 Mask 三分类直接宣称得到三维脊柱或 Cobb 测量 |

本轮不执行上述部署、打包或新模型工作。下一阶段应按明确的使用场景选择一条路线，并保留本次可运行、可复核的本机基线。
