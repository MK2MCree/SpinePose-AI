# 脊见智诊 · 手机单机记录工具

这是独立的鸿蒙原生手机工程。现有 `web/`、`site/` 和 Python 后端不参与手机构建，原功能未被修改。

2026-10-30：手机版首页、记录、拍摄引导、编辑和设置页已对齐网页品牌，直接复用原有 Logo，采用白紫视觉，并调整小屏排版、固定底部操作与键盘避让。详情见 `UI_UPDATE.txt`；`design/ui-preview.png` 为视觉预览，并非运行截图。

- 应用名称：**脊见智诊**。
- 公开支持邮箱：**benjaminmcree@qq.com**。
- 技术：ArkTS / ArkUI、本地 Preferences、系统相机与图片/文件选择器。
- 已有记录功能：新增、编辑、删除、日期排序、七天时长汇总、JSON 导出与导入、清空记录。
- 本轮增加：拍前视角引导、系统拍照、本机图片选择，以及记录中的缩略图、来源、视角和本地路径。
- 首次启动为空；无注册登录、网络请求、模型服务、第三方广告或统计 SDK。

工程在 [`harmonyos/`](harmonyos/)，上架操作记录在 [`RELEASE_GUIDE.md`](RELEASE_GUIDE.md)，图片功能规格在 [`CAPTURE_FEATURE_SPEC.md`](CAPTURE_FEATURE_SPEC.md)。0.2.0 已完成拍摄和图片附件实现，真实鸿蒙编译通过，29 项记录测试与 15 项照片服务测试通过；实际系统拍照和选图尚待设备验收。

后台已创建 APP ID，工程包名为 `com.benjaminmcree.spineinsight`；公开应用标识记录在 [`release-identity.json`](release-identity.json)。当前尚未完成正式上架。应用标识记录不包含 Client Secret、私钥或 SDK 凭据。

图片复制进应用自己的文件目录，保存在本机。JSON 备份只包含记录和图片元数据，不含图片文件；卸载或清除应用数据会移除应用保存的记录与图片。
