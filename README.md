# 微信聊天导出器

Windows 本地微信聊天记录预览与单文件 HTML 导出工具。

**下载程序：[Releases](https://github.com/Ffive55/wechat-chat-exporter/releases/latest)**

当前版本：**1.0.2**。

## 功能

- 指定 `xwechat_files` 根目录，选择其下的微信账号。
- 账号列表显示微信昵称和头像，记住所选目录及账号。
- 左侧搜索会话，右侧预览聊天记录、头像、照片和表情。
- 按北京时间筛选，包含起止时刻，精确到秒。
- 导出单个 HTML，内嵌文字、头像、图片和表情，离线可查看。
- 照片缓存自动解密；JPEG、PNG、GIF 保留原字节。WXGF 同时保留原始明文数据和显示用 JPEG。
- 内置 109 种微信表情资源；已知标签显示为小图，文字标签仍可搜索。
- “跳到最近”定位到当前筛选范围的最后一条消息，并在媒体延迟加载时保持底部。

## 使用

1. 登录电脑版微信。
2. 从 Releases 下载 Windows x64 EXE 并运行，无需安装 Python。
3. 点击“目录”，选择包含各账号文件夹的 `xwechat_files` 根目录。
4. 选择账号，点击“读取 / 更新记录”。首次读取会建立本地索引。
5. 选择会话和时间范围，点击“导出单文件 HTML”。

未读取过、且没有本地资料的账号先显示占位名；读取后补齐昵称和头像。预览每批显示 200 条，导出不受预览分页限制。

## 环境与功能范围

- Windows 64 位；已在 **Windows 10 x64、电脑版微信 4.1.15.13** 上验证。
- 界面需要 Microsoft Edge WebView2 Runtime。
- 图片解码使用用户本机微信的 `VoipEngine.dll`，本仓库及 Release 不分发该文件。
- 微信版本变化可能影响数据库读取或图片解密。
- 语音、视频及文件消息保留消息记录和原始消息内容，当前不嵌入对应附件本体。
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

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 项目结构

- `main.py`：程序入口、窗口和单实例控制。
- `api.py`：界面接口、账号选择及后台任务。
- `storage.py`：数据库读取、解密、索引与时间筛选。
- `media.py`、`media_crypto.py`、`codec_worker.py`：图片、表情、头像和隔离解码。
- `rendering.py`：单文件 HTML 导出。
- `assets/`：界面、图标与内置表情。
- `vendor/`：复用的 wxlocal 代码。
- `tests/`：账号根目录和选择状态测试。

## 许可与第三方资源

本项目包含 wxlocal 的衍生代码，按 **AGPL-3.0** 提供源码，详见 [LICENSE](LICENSE)。第三方代码及资源说明见 [THIRD_PARTY.txt](THIRD_PARTY.txt)。微信表情图片版权归腾讯公司所有，资源仓库说明其用于个人学习和非商业用途；软件许可不代表获得这些图片的商业授权。
