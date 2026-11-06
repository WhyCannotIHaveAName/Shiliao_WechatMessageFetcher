# 拾聊 2.4 Windows 版

解压整个 `拾聊-2.4-Windows.zip`，然后打开 `Shiliao-2.4\Shiliao-2.4.exe`。在导出的 HTML 里点击「展开聊天记录」即可阅读合并转发中的消息。如果里面还有一份聊天记录，可以继续展开。TXT 用缩进表示层级，JSON 用 forwarded 数组保存各层消息。只有摘要或缺少本地图片时会保留相应说明。

2.2 版支持从本机微信缓存识别图片密钥，适用于迁移到其他盘的账号目录。已用真实迁移目录验证图片导出，详细范围见 `TESTING.md`。

此版本直接读取电脑上的微信 4.x 数据库，不需要选择、转发消息，没有 100 条上限。导出范围是当前电脑已经保存的聊天记录；手机独有或已经删除的记录不会凭空恢复。当前适配在 Windows 微信 4.2.0.11 上验证。

## 使用

1. 解压整个压缩包，保留可执行文件旁的 `_internal` 文件夹。
2. 保持电脑微信已登录，双击 `Shiliao-2.4.exe`。
3. 点击“读取本地记录”。自动查找失败时，选择微信设置中显示的 `xwechat_files` 数据目录或其中的账号文件夹。多个账号时可选择具体账号目录。
4. 点击列表中的会话勾选，可搜索、全选当前结果。输入日期可选，格式 `YYYY-MM-DD`，结束日期包含当天。
5. 选择保存位置，点击“导出所选会话”。完成后点击“打开导出文件夹”，打开其中的 `打开聊天记录.html`。

每个会话都有 HTML、UTF-8 TXT、JSON 和图片 assets 文件夹。复制导出结果时请复制整个文件夹。JSON 保留原始消息内容、类型码、发送者账号、显示名和时间戳；HTML 中非文本消息可展开“原始消息信息”。群聊按每条消息的发信方映射显示，无法确定时保留账号或显示未知发送者。

## 图片与其他类型

- 图片优先寻找本机原图，其次本地图片和缩略图；结果会标注图片质量。缺失或解码失败会明确标注，不会伪装成成功。
- WXGF 图片保留原始容器，尝试生成首帧 JPEG 预览；这不是动画完整转码。普通 GIF 原样保存。
- 动画表情保留可获得的说明、链接或 MD5。没有说明时不会猜测“OK”等含义，不保证另存动画表情图片。
- 红包、转账、链接、小程序、引用等解析标题、描述或网址，同时保留完整原始内容。聊天记录卡片解析内嵌发信方、时间和文字，内嵌图片按本机附件标识匹配，缺图时保留说明。
- 语音、视频、文件等保留类型及原始描述，本版不导出其二进制附件。

## 运行与取消

读取和导出在后台线程执行，界面可取消任务。退出时等待任务停止并清理临时数据库快照。强制终止程序或断电可能留下系统临时目录中的 `Shiliao-*` 快照。取消或失败的导出保存在带 `.未完成` 后缀的文件夹，统计状态为 incomplete，请勿当作完整结果。

程序不发送微信消息，不修改微信数据库，不上传记录，不自动访问消息里的链接。数据库快照只读读取；数据库密钥和图片密钥不写入配置或导出文件。导出件包含你选择的聊天内容，请按自己的需要保存。

## 从源码运行和构建

需要 Windows x64、Python 3.12，带 Tkinter。仅从 PyPI 安装下面的依赖；不需要下载第三方微信工具安装包。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python fetch_tk_resources.py
.\.venv\Scripts\python app.py
.\.venv\Scripts\python test_export.py
$env:TCL_LIBRARY='vendor/tcl8.6'
$env:TK_LIBRARY='vendor/tk8.6'
.\.venv\Scripts\python -m PyInstaller --noconfirm Shiliao.spec
$exe = Resolve-Path .\dist\Shiliao-2.4\Shiliao-2.4.exe
$report = Join-Path (Get-Location) 'packaged-test.json'
$p = Start-Process $exe -ArgumentList @('--self-test', ('"' + $report + '"')) -PassThru -Wait
if ($p.ExitCode -ne 0 -or !(Get-Content -Raw $report | ConvertFrom-Json).passed) { throw 'Self-test failed' }
.\.venv\Scripts\python package_release.py
```

压缩包生成在项目的 outputs 目录。

## 开源来源

部分 Windows 数据定位、数据库密钥提取代码和媒体格式参考来自 `fanyuantaier/wechatauto-replica`，固定提交 `bc2e6f2461330eb1a53996d2f2afc18416376b52`：
https://github.com/fanyuantaier/wechatauto-replica/tree/bc2e6f2461330eb1a53996d2f2afc18416376b52

采用 Apache-2.0 许可，见 `LICENSE-Apache-2.0.txt`、`NOTICE.txt`。本项目隔离为只读导出，加入分页校验、WAL 已提交事务恢复、取消控制、界面、流式导出与测试。程序依赖的第三方许可随打包文件保存。
