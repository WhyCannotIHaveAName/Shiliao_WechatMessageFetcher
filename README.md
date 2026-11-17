# 拾聊

| 平台 | 下载 | 用途 |
| --- | --- | --- |
| Windows 2.6.1 | [下载 ZIP](https://github.com/WhyCannotIHaveAName/Shiliao_WechatMessageFetcher/releases/download/v2.6.1/Shiliao-2.6.1-Windows.zip) | 读取电脑微信的本地聊天记录和朋友圈 |
| Android 2.6.1 预览版 | [下载 APK](https://github.com/WhyCannotIHaveAName/Shiliao_WechatMessageFetcher/releases/download/v2.6.1-android-preview.1/Shiliao-Android-2.6.1-preview.1.apk) | 在手机上导入、搜索、收藏和再导出；Android 8.0 及以上 |

Android 版通过导入电脑拾聊导出的 ZIP 或 JSON、手动收藏和系统分享获取内容。要导入照片，请把含 JSON 和 `assets` 的整个导出目录压成 ZIP，再传到手机。详见 [Android 使用与构建说明](android/README.md)。

## Windows 2.7 验收版

解压整个验收 ZIP，然后打开 `Shiliao-2.7\Shiliao-2.7.exe`。上方 Windows 下载链接指向已发布的 2.6.1。

聊天记录和朋友圈各有独立工作区，可按需读取、筛选和导出为 HTML、TXT、JSON。朋友圈支持按正文、发布者和分享链接搜索。导出按钮固定在底栏。

2.2 版支持从本机微信缓存识别图片密钥，适用于迁移到其他盘的账号目录。已用真实迁移目录验证图片导出，详细范围见 `TESTING.md`。

此版本直接读取电脑上的微信 4.x 数据库，无需手动选择、转发微信里的消息。导出范围以当前电脑已经保存的记录为准。2.6.1 在 Windows 微信 4.2.0.13 上验证了聊天与朋友圈独立读取。

## 使用

1. 解压整个压缩包，保留可执行文件旁的 `_internal` 文件夹。
2. 保持电脑微信已登录，双击 `Shiliao-2.7.exe`，在侧栏选择「聊天记录」。
3. 点击「读取聊天记录」。自动查找失败时，选择微信设置中显示的 `xwechat_files` 数据目录或其中的账号文件夹。多个账号时可选择具体账号目录。
4. 点击列表中的会话勾选，可搜索、全选当前结果。可手填时间范围，也可点击“今天”“昨天”“一周前至今”“全部时间”。日期格式 `YYYY-MM-DD`；小时格式 `YYYY-MM-DD-H`，例如 `2026-11-09-1` 表示当天 1:00。小时为 0–23，留空不限。开始时间包含在内，结束整点不包含；仅填结束日期时包含全天。
5. 选择保存位置，点击“导出所选会话”。完成后点击“打开导出文件夹”，打开其中的 `打开聊天记录.html`。

小窗口内可用滚轮或右侧滚动条查看导出设置。导出、取消和打开文件夹按钮固定在底栏。

每个会话都有 HTML、UTF-8 TXT、JSON 和图片 assets 文件夹。复制导出结果时请复制整个文件夹。JSON 保留原始消息内容、类型码、发送者账号、显示名和时间戳；HTML 中非文本消息可展开“原始消息信息”。群聊按每条消息的发信方映射显示，无法确定时保留账号或显示未知发送者。

在聊天记录 HTML 里点击「展开聊天记录」即可阅读合并转发中的消息，嵌套记录可以继续展开。TXT 用缩进表示层级，JSON 用 `forwarded` 数组保存各层消息。只有摘要或缺少本地图片时会保留相应说明。

例如开始填 `2026-11-09-1`、结束填 `2026-11-09-2`，导出当天 1:00 至 2:00 之前的消息。“今天”“昨天”选择对应整天；“一周前至今”从本机日期的七天前 0:00 到今天结束；“全部时间”清空两端。时间按电脑本地时区解释。

## 导出朋友圈

1. 登录电脑微信后，在拾聊侧栏选择「朋友圈」，点「读取朋友圈」。界面显示本账号的本地朋友圈条数。若需要更早的动态，可先在电脑微信中打开相应朋友圈并翻到目标日期，再点「读取朋友圈」。只有写入本机库的动态才会纳入导出。
2. 在关键词框按正文、发布者或分享链接搜索。预览显示最新 200 条匹配结果。选好时间范围与保存位置，点击「导出朋友圈」，即可导出同时符合关键词和时间条件的全部结果。
3. 点「打开导出文件夹」，打开 `朋友圈.html`。同目录有 `朋友圈.txt`、`朋友圈.json`、`导出统计.json` 和 `assets`。结果按发布时间从新到旧排列，保留发布者、文字、分享链接及本地可匹配图片。JSON 同时保留 Unix 时间戳。时间范围启用时，发布时间未知的记录会计入排除统计。

打开「导出本地图片」时，拾聊优先用图片内容的 MD5 校验。原图哈希不匹配时，仅在宽、高和字节数相等且匹配唯一的情况下保存候选图。候选图在 HTML 中折叠显示，需对照微信核对。未匹配的图片会在动态下标明。缺少原图很常见，微信本地可能只保留缩略图或重新编码后的版本。

勾选「附带全部图片缓存（所有日期）」可输出 `本机图片缓存.html` 和所有可解码的本机朋友圈图片缓存。此选项独立于动态的关键词和日期范围，图片来自本地缓存，按内容去重。缓存文件没有可靠的动态编号或发布时间。朋友圈内容与图片的读取均在本机完成。HTML 只在你主动点击分享链接时打开外部页面。

勾选「导出本地评论及回复」后，HTML 可展开评论，TXT 列出评论时间和回复对象，JSON 保留评论 ID、时间戳和已确认的原评论 ID。日期条件筛选动态，所选动态下本机可读的评论一并导出。可匹配的回复可在 HTML 里跳转到原评论，缺少原评论时保留能读到的引用信息。评论中的图片和表情保留附件数量。评论开关默认关闭。

## 趣味统计

在侧栏选择「趣味统计」，设定时间范围和数据来源，点击「生成统计」。可单独分析聊天或朋友圈。

- **年度人物与私聊排行**依据本机双向私聊记录。年度人物按所选区间与每个自然年的交集计算，第一名并列时会标注。私聊排行展示前 12 位。
- **聊天常用字词与表情**可切换大家或本人的榜单。中文词用 Jieba 离线分词，微信表情按文本里已知的方括号名称统计，例如 `[呲牙]`。本人统计使用与账号目录和联系人相符的发送者账号。取消「统计聊天字词和表情」可以直接生成数量排行。
- **朋友圈发帖排行**比较同一时间范围内各发布者的本机动态数量，展示前 12 位。

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
$exe = Resolve-Path .\dist\Shiliao-2.7\Shiliao-2.7.exe
$report = Join-Path (Get-Location) 'packaged-test.json'
$p = Start-Process $exe -ArgumentList @('--self-test', ('"' + $report + '"')) -WindowStyle Hidden -PassThru -Wait
if ($p.ExitCode -ne 0 -or !(Get-Content -Raw $report | ConvertFrom-Json).passed) { throw 'Self-test failed' }
.\.venv\Scripts\python package_release.py
```

压缩包生成在项目的 outputs 目录。

## 开源来源

部分 Windows 数据定位、数据库密钥提取代码和媒体格式参考来自 `fanyuantaier/wechatauto-replica`，固定提交 `bc2e6f2461330eb1a53996d2f2afc18416376b52`：
https://github.com/fanyuantaier/wechatauto-replica/tree/bc2e6f2461330eb1a53996d2f2afc18416376b52

采用 Apache-2.0 许可，见 `LICENSE-Apache-2.0.txt`、`NOTICE.txt`。本项目隔离为只读导出，加入分页校验、WAL 已提交事务恢复、取消控制、界面、流式导出与测试。程序依赖的第三方许可随打包文件保存。

朋友圈数据库表和本地缓存目录的格式参考 [wechatauto-replica 的朋友圈实现](https://github.com/fanyuantaier/wechatauto-replica/blob/bc2e6f2461330eb1a53996d2f2afc18416376b52/wechatauto/moment.py)。

中文词频使用 [Jieba](https://github.com/fxsjy/jieba)，其许可证随 Windows 验收包附带。
