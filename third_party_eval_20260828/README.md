# 第三方项目隔离评估区

创建日期：2026-08-28。此目录与产品源码隔离，用于许可、运行条件和融合可行性审计。

## 获取结果

已尝试以 `git clone --depth 1` 下载以下仓库：

| 项目 | 固定提交 | 许可证 | 本地状态 | 可用于产品 |
| --- | --- | --- | --- | --- |
| [RepDetect](https://github.com/giaongo/RepDetect) | `50f990f138de50778833f82b812e4a3691eacaef` | Apache-2.0 | 核心源码快照 | 是，需保留版权和许可证 |
| [OpenCap Core](https://github.com/opencap-org/opencap-core) | `4c0aab5e2f1c80810585cda212769e9da4ec167e` | Apache-2.0 | 入口与复现资料快照 | 仅后续专业/科研模式 |
| [SCOLISCAN](https://github.com/ScreeningSystem/SCOLISCAN) | `d25c9505b6eb2ad8e9f7d2cf73a468e5c9f499c3` | 未声明 | 只做远程审计 | 否，只借鉴产品问题与交互思路 |
| [ScolioVis](https://github.com/Blankeos/scoliovis) | `67ebe2c6df6634ffcd1b6bb9dab5f0bed64cd4b9` | 未声明 | 只做远程审计 | 否，只作为算法与报告流程对标 |
| [Spine Cobb Angle Measurement](https://github.com/LijunRio/Spine-cobb-angle-measurement) | `dffd9ae2b0ee2151e10006e18afc27679645430d` | 未声明 | 只做远程审计 | 否，只作为传统算法基线参考 |

最初随 Codex 运行时提供的 Git 缺少 `git-remote-https`；完整 Git 已安装，直接下载公开 ZIP 仍被网络出口重置。因此没有伪造 Git 历史。许可证明确的项目通过 GitHub 连接器取得核心可审计源码，并以提交 SHA 固定到 `apache_source_snapshots/`。

### Git 修复记录

- 已安装完整 Git for Windows 2.55.0.3：`<LOCAL_WORKSPACE>`；
- 已验证 HTTPS 远程助手存在：`mingw64/libexec/git-core/git-remote-https.exe`；
- GitHub 克隆仍无法完成的原因已从 Git 组件问题变为网络出口问题：当前会话的代理指向不可用的 `127.0.0.1`，临时绕过后直连 GitHub 仍被连接重置；
- 因此继续使用已固定提交的 GitHub 连接器源码快照，不将失败的半克隆内容视作可复现依赖。

## 核验结论

当前环境为 Python 3.13.9，未检测到 Android SDK、`adb`、`opensim-cmd` 或 `ffmpeg`。机器 GPU 为 RTX 4050 Laptop（6141 MiB 显存）：满足当前项目的轻量图像工作流，但低于 OpenCap 文档列出的常规 OpenPose 本地处理建议（约 8GB）。因此下述“可复现”区分为可在产品中独立复现、需专用隔离环境复现，以及仅供参考三类。

### RepDetect：第一期可提炼

- 原项目以 Android/CameraX/ML Kit 实现实时姿态检测。
- 其核心为姿态分类置信度、平滑与滞回阈值计数，再将训练计划和结果写入本地数据库。
- 不能直接并入本项目：语言为 Kotlin、依赖 Android SDK，且 Gradle 同时声明旧版和 beta 版 ML Kit/CameraX，直接构建存在依赖漂移风险。
- 可以安全提炼：在当前 Web 端重新实现“浏览器姿态关键点 → 自建动作状态机 → 去抖计数 → 服务端训练记录”，不搬运 Android UI 与样本。
- 本机不具备 Android 构建环境，故没有把 Android APK 的构建成功误报为已验证；原项目的核心计数机制已提炼为本目录的独立、可测试原型。

### SCOLISCAN：交互思路可提炼，源码不可用

- 原型通过浏览器摄像头和 MediaPipe Pose Landmarker 计算肩线、骨盆线与躯干偏移，并显示采集质量和复测提示。
- 其阈值在代码中明确是原型启发式，README 也说明不是临床验证的诊断阈值。
- 无许可证：不复制其 JavaScript、UI、图标、阈值或训练建议；本项目只自行实现“采集引导、质量门控、非诊断性记录”的产品能力。

### ScolioVis 与传统 Cobb 工具：仅对标

- ScolioVis 的工作流是 AP X 光关键点检测、关键点几何计算 Cobb 角与报告；其仓库、权重和引用数据集均无可复用许可证。
- Hough 线基线通过图像边缘和直线候选计算角度，缺少临床端板选择和有效性验证，不能作为产品临床结论。
- 两者都只能用于制定独立研发的验收项和人工复核流程，不能直接并入。

### OpenCap：可复现但不适合第一期融合

- 流程为：多相机标定 → 2D 姿态 → 三角化 → 标记点增强 → OpenSim 运动学。
- 上游要求两路以上近同步视频、OpenSim、OpenPose/MMpose、FFmpeg 和 NVIDIA GPU；其说明中，常规配置需要至少约 8GB GPU。
- 它的价值是后续“专业动作评估”模式，不是普通用户每天打开一次的 Web 功能。
- 当前设备缺少 OpenSim/FFmpeg 且显存为约 6GB；若未来采用，应放进 Docker/独立 GPU 服务，绝不与现有 FastAPI 推理进程共用运行环境。

## 新增方向：照片、X 光、CT/MR 与文字对话

这一轮不把“照片能检测脊柱”与“影像能辅助阅片”混为同一能力。照片只适用于体表姿态采集和纵向对比；X 光、CT、MR 的输出必须标记为算法候选并进入人工复核，不能直接形成诊断。

| 方向 | 审计项目 | 许可证与状态 | 能复用的内容 | 融入结论 |
| --- | --- | --- | --- | --- |
| 全脊柱 X 光 | [Vertebra Landmark Detection](https://github.com/yijingru/Vertebra-Landmark-Detection) | MIT；最后提交 2020-12-16；依赖 PyTorch 1.1/Python 3.6，模型和数据另行下载 | 椎体关键点→几何角度候选→覆盖层复核的算法流程 | 推荐作为二期独立重实现/迁移验证，不能直接把旧依赖并进主环境；数据集和预训练权重的授权须另审 |
| CT / MR | [TotalSegmentator](https://github.com/wasserth/TotalSegmentator) | Apache-2.0；最近提交 2026-08-13；支持 DICOM 文件夹或 NIfTI | 椎体 C1–L5、椎间盘与相关肌肉的 3D 分割、预览和量化输出 | 推荐作为专业影像的独立后台 worker；它声明不是医疗器械，结果只能是可视化/复核候选 |
| 脊髓 MR | [Spinal Cord Toolbox](https://github.com/spinalcordtoolbox/spinalcordtoolbox) | LGPLv3；研究型 MRI 工具 | 脊髓/灰质/病灶分割、椎体水平定位与定量 MRI 流程 | 只适用于科研/专科脊髓 MR；不静态复制或并入主进程，若接入应作隔离命令行服务并完成 LGPL 合规评估 |
| 体表照片 | [SCOLISCAN](https://github.com/ScreeningSystem/SCOLISCAN) / [SlicerIGT Scoliosis](https://github.com/SlicerIGT/Scoliosis) | 两者仓库均未声明可复用许可证 | 相机引导、左右对称指标、复测工作流 | 只借鉴体验；自行以 MediaPipe 等许可明确的基础组件实现“姿态记录”，不称作脊柱诊断 |
| 文本对话 | [Back Pain RAG Chatbot](https://github.com/MouadBhih/BACK_PAIN_RAG_CHATBOT) | README 标记 MIT，但仓库无独立 LICENSE，不能直接复制 | 文献切片→向量检索→引用回答→红旗症状提醒的流程 | 推荐自行实现为本项目 FastAPI 模块，不复用其 notebook；必须加入中文红旗分诊、来源可见与人工转介 |
| 文本对话（反例） | [PhysioGPT](https://github.com/Mayank-bhardwaj02/PhysioGPT) | 未声明许可证 | 结构化问诊问题清单可供体验研究 | 不接入：全局会话状态会串用户，且使用危险的本地向量库反序列化设置，不能满足多用户安全性 |

推荐产品层级：先做“文字结构化分诊 + 体表姿态记录 + 医生已确认训练计划”，再做“X 光关键点覆盖与人工复核”，最后才把 CT/MR 的分割作为异步专业模式。所有层均应保存输入来源、协议版本、模型版本、置信度、人工复核状态和不可比原因，避免把不同拍摄方式或不同算法版本混入同一趋势图。

## 可运行原型

`proof_of_concept/spine_agent_logic.py` 独立验证了三个本项目必须具备的数学/业务规则：

1. 体态记录必须先通过单人、取景、关键点可见度质量门控；
2. 训练建议不能绕过版本化分诊协议；
3. 趋势只比较同一拍摄方法和同一协议版本的测量；动作计数采用参数化滞回，避免抖动重复计数。

运行：

```powershell
& '<LOCAL_WORKSPACE>' -m unittest discover -s third_party_eval_20260828\proof_of_concept -p 'test_*.py' -v
```
