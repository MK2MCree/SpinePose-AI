# SpinePose-AI / 脊见智诊 SpineInsight

面向 X 光、DICOM 与 RGB 体表图像的脊柱智能辅助筛查、量化分析与报告平台。

> 本项目用于辅助研究、教学和产品验证，不构成医疗诊断或治疗建议。任何临床使用都需要完成数据合规、模型验证、医疗器械法规评估和专业人员复核。

## 在线版本

GitHub Pages 提供零成本的静态产品展示，包括官网、工作台界面和完整交互设计。由于 GitHub Pages 不能运行 Python、PyTorch 或持久化后端服务，在线展示版不会处理或上传医学影像。

在线访问：[SpineInsight GitHub Pages](https://mk2mcree.github.io/SpinePose-AI/)

完整功能可在本机或自行部署的服务器上运行：

- DICOM 与常规 X 光图像导入、窗宽窗位和脊柱量化分析
- RGB 体表图像检测、有效视野裁剪和批处理
- 迁移版与源域直推版伪 CT 生成
- 医生版与个人版 PDF 报告
- 会话级病例隔离、私有结果访问和批任务恢复

## 架构

```text
Browser / GitHub Pages
        |
        | HTTPS（完整部署时）
        v
FastAPI API + signed session cookie
        |
        +-- X-ray / RGB inference
        +-- pseudo-CT generation
        +-- PDF report generation
        +-- private case and job storage
```

前端静态资源位于 `web/`，Sites/GitHub Pages 发布资源位于 `site/public/`；FastAPI 服务入口为 `main.py`，生产配置集中在 `mindspine/`。

## 本机启动

建议使用 Python 3.10。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env
python main.py
```

浏览器访问 `http://127.0.0.1:8000/`。生产部署可使用仓库中的 `Dockerfile` 与 `docker-compose.yml`。

## 模型文件

模型权重总量约 755 MB，并且单文件超过 GitHub 普通仓库限制，因此不会提交到公开仓库。所需路径和 SHA-256 校验值记录在 `weights/manifest.json`。获得合法模型文件后放入对应目录，并运行：

```bash
python scripts/verify_models.py
```

## 安全说明

- `.env`、`app_settings.json`、病例数据、生成结果和模型权重均被排除在公开仓库之外。
- 生产环境必须设置新的会话密钥与代理密钥，不得复用开发配置。
- 外部大模型增强默认关闭；启用前应取得用户授权并完成隐私评估。
- 不要将真实患者资料提交到 Issue、日志、测试样例或公开仓库。

安全问题请通过私下渠道联系仓库所有者，不要创建包含敏感数据的公开 Issue。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tests/smoke_http.py
```

完整回归脚本为 `tests/full_regression_http.py`。

## 许可

本仓库当前未授予开源许可证。除法律明确允许的情形外，未经仓库所有者书面许可，不得复制、修改、再分发或商业使用。
