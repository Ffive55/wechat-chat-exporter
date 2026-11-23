# 微信聊天导出器

Windows 本地微信聊天记录预览工具，支持 HTML、PDF 和 Markdown 导出。

**下载程序：[Releases](https://github.com/Ffive55/wechat-chat-exporter/releases/latest)**

当前版本：**1.1.4**。

本版修复视频号及升级提示卡片的标题、分类与链接解析，并统一预览和导出结果，详细变更见 [更新日志](CHANGELOG.md)。

## 功能

- 指定 `xwechat_files` 根目录，选择其下的微信账号。
- 账号列表显示微信昵称和头像，记住所选目录及账号。
- 左侧搜索会话，右侧预览聊天记录、头像、照片和表情。
- 按北京时间筛选，包含起止时刻，精确到秒。
- HTML 单文件导出：内嵌文字、头像、图片和表情，离线可查看。
- PDF 导出：A4 纵向分页，保留聊天气泡、头像和图片，正文可检索、复制；GIF 显示首帧。
- Markdown（.md）导出：默认文字模式，可选附带照片、自定义表情和微信内置表情；内置表情按 22px 行内小图显示，保留标签，重复资源去重，GIF 保留动画。
- 记住上次成功导出的文件格式，下次启动自动恢复；取消或失败不更新。
- 日历选择日期时，开始默认 00:00:00、结束默认 23:59:59；手动修改时间后，更换日期保留时分秒。
- 照片缓存自动解密；HTML 与带图 Markdown 中的 JPEG、PNG、GIF 保留原字节。HTML 同时保留原始 WXGF 数据和显示用 JPEG。
- 内置 109 种微信表情资源；已知标签显示为小图，文字标签仍可搜索。
- 自定义表情支持微信 `qq.com`、`qpic.cn` 资源地址和群聊消息格式，保留 GIF 动画；下载后可从本地缓存离线导出。
- 支持普通表情消息及 `49 / 8` 格式的 GIF 表情，预览和 HTML 导出均保留原始动画，不再显示 `sticker_gif` 占位文字。
- “跳到最近”定位到当前筛选范围的最后一条消息，并在媒体延迟加载时保持底部。

## 使用

1. 登录电脑版微信。
2. 从 Releases 下载 Windows x64 EXE 并运行，无需安装 Python。
3. 点击“目录”，选择包含各账号文件夹的 `xwechat_files` 根目录。
4. 选择账号，点击“读取 / 更新记录”。首次读取会建立本地索引。
5. 选择会话和时间范围，从“导出格式”下拉框选择 HTML、PDF 或 Markdown，然后点击“导出”。
6. Markdown 默认只导出文本与媒体占位；勾选“附带图片和表情”后，`[笑脸]`、`[破涕为笑]` 等已知内置表情也会导出为行内图片，并生成 `.md` 与同名 `_assets` 文件夹，移动或分享时请同时携带。
7. 完成后可点击“打开导出文件”或“打开所在文件夹”。

日期与时分秒在同一组控件中分别设置。点击时间右侧的时钟图标，可在与日期选择器相同风格的原生面板中选择时、分、秒，也可直接输入时间。选择日期不会改动手动设置的时间；清空日期、“全部时间”或切换会话/账号后，恢复全天默认时间。未选日期表示该侧不限。

未读取过、且没有本地资料的账号先显示占位名；读取后补齐昵称和头像。预览每批显示 200 条，导出不受预览分页限制。

## 环境与功能范围

- Windows 64 位；已在 **Windows 10 x64、电脑版微信 4.1.15.13** 上验证。
- 界面需要 Microsoft Edge WebView2 Runtime。
- 图片解码使用用户本机微信的 `VoipEngine.dll`，本仓库及 Release 不分发该文件。
- 微信版本变化可能影响数据库读取或图片解密。
- 语音、视频及文件消息保留可读记录，当前不嵌入对应附件本体；HTML 另保留原始消息内容。
- 缺失或无法唯一匹配的图片会明确标注，不会猜测配图。

## 本地数据

运行数据默认保存在 `%LOCALAPPDATA%\WeChatLocalExporter`，也可指定：

```powershell
.\微信聊天导出器.exe --data D:\我的微信归档
```

程序只读取微信源数据库和媒体，不修改或删除源文件。消息索引、解密副本、缓存和日志保存在程序数据目录。数据库密钥只在运行内存中使用。

“联网补全表情”只下载消息携带的微信表情资源，并验证 MD5；可关闭后仅使用本地缓存。聊天正文不会上传。

## 从源码运行和构建

需要 64 位 Python 3.12 与 PowerShell 7：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

构建单文件 EXE：

```powershell
pwsh -File .\build.ps1 -Python .\.venv\Scripts\python.exe
```

产物位于 `dist\微信聊天导出器.exe`。EXE 和其他构建产物通过 Releases 分发，不提交到源码分支。

需要保留正在运行的旧版时，可用 `-DistPath .\dist\1.1.4` 指定输出目录。构建脚本会一并收集当前 Python SSL 初始化需要的证书策略文件，避免打包后首次下载表情时报“文件不存在”，并收集项目许可证、第三方声明及其附带许可证。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

真实 WebView2 PDF 与打包产物验证（仅使用脚本生成的测试数据；`pypdf` 只用于验收，不是应用运行依赖）：

```powershell
.\.venv\Scripts\python.exe -m pip install pypdf
.\.venv\Scripts\python.exe tests\verify_pdf_runtime.py --root tmp\verify-pdf
.\.venv\Scripts\python.exe tests\verify_frozen_exports.py --exe dist\微信聊天导出器.exe --root tmp\verify-frozen
```

验证包括同一窗口连续两次 PDF 导出、A4 页面尺寸、105 行跨页文字完整性、视频号与直播卡片、实际链接及升级占位过滤；打包验证另覆盖 HTML、两种 Markdown 导出和重启后的格式记忆。

打包后的联网验证使用独立空缓存，并临时禁用消息中的资源地址来验证缓存加载与导出；源数据库只读：

```powershell
.\.venv\Scripts\python.exe tests\verify_frozen_download.py --exe .\dist\微信聊天导出器.exe --data "$env:LOCALAPPDATA\WeChatLocalExporter" --message-id <自定义表情消息ID>
```

可重复传入 `--message-id`，选择不同 MD5、不同资源域名的表情进行验证。

该验证同时支持普通表情消息和 `49 / 8` 格式的 GIF 表情。浏览器界面验证脚本位于 `tests/verify_sticker_ui.cjs`，可配合 `dev_server.py` 和本地测试数据，检查预览加载、HTML 内嵌字节、动画播放及离线打开时的网络请求。

## 项目结构

- `main.py`：程序入口、窗口和单实例控制。
- `api.py`：界面接口、账号选择及后台任务。
- `storage.py`：数据库读取、解密、索引与时间筛选。
- `media.py`、`media_crypto.py`、`codec_worker.py`：图片、表情、头像和隔离解码。
- `rendering.py`：单文件 HTML 导出及共用消息渲染。
- `pdf_export.py`：打印页面排版与 WebView2 PDF 导出。
- `markdown_export.py`：Markdown 与可选资源文件夹导出。
- `assets/`：界面、图标与内置表情。
- `vendor/`：复用的 wxlocal 代码。
- `tests/`：账号选择、自定义表情获取与离线导出测试。

## 许可与第三方资源

本项目包含 wxlocal 的衍生代码，按 **AGPL-3.0** 提供源码，详见 [LICENSE](LICENSE)。第三方代码及资源说明见 [THIRD_PARTY.txt](THIRD_PARTY.txt)。微信表情图片版权归腾讯公司所有，资源仓库说明其用于个人学习和非商业用途；软件许可不代表获得这些图片的商业授权。

wxlocal 原仓库暂不可访问时，可查阅备用上游 [KurauWoo/wxlocal_fork](https://github.com/KurauWoo/wxlocal_fork)，已核验源码基准为 `fecdc7e586cc3a5007e8470c6455c257fb0849e6`。该地址用于追溯和比较代码，本项目保留自己的修复，不自动覆盖更新。消息卡片解析还参考了 [dhammavipassi/wx-cli-again](https://github.com/dhammavipassi/wx-cli-again) 的修复，具体提交及许可见第三方说明。
