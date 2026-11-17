# 拾聊 Android 2.7 验收版

当前在 GitHub 发布的版本为 [Android 2.6.1 签名 APK](https://github.com/WhyCannotIHaveAName/Shiliao_WechatMessageFetcher/releases/download/v2.6.1-android-preview.1/Shiliao-Android-2.6.1-preview.1.apk)。下文的新功能适用于 2.7 验收 APK。将文件传到手机后，可在系统文件管理器中打开安装。使用相同签名的 2.6.1 可以覆盖升级，保留已导入档案。安装包声明支持 Android 8.0 及以上。

## 导入电脑档案

1. 用 Windows 拾聊完成一次导出。把**整个导出结果目录**压缩为 ZIP，保留 `朋友圈.json` 或各会话的 `聊天记录.json` 及同目录的 `assets`。把 ZIP 传到手机。
2. 打开拾聊，点「导入」并选择 ZIP。也可导入单个拾聊 JSON 来查看文字、时间和链接。照片需要随 ZIP 提供。
3. 切换「聊天记录」或「朋友圈」，从下拉列表选择档案。关键词和时间范围可叠加，点「导出 ZIP」保存筛选结果。ZIP 含 HTML、TXT、JSON 和随记录关联的可用附件。
4. 如果档案含 Windows 版导出的缓存图册，可在朋友圈页点「图册」查看。动态里的图片匹配状态沿用原档案。

## 朋友圈评论

先在 Windows 2.7 导出时勾选「导出本地评论及回复」。手机导入后，可在动态下展开「评论」，查看文字、时间和回复关系；能定位到原评论的回复可以点击跳转。

点「导出 ZIP」时，「包含档案内的评论及回复」默认关闭。勾选后，HTML 显示可展开的评论和跳转，TXT 保留评论时间与回复对象，JSON 保留时间戳、评论 ID 和引用关系。日期筛选针对动态，该动态下已导入的评论一并保留。评论图片和表情沿用 Windows 档案中的附件数量。

## 趣味统计

在顶部点「趣味统计」，选择「生成统计」。默认分析全部已导入档案，可在「范围与来源」分别选聊天或朋友圈、指定档案和日期，或关闭聊天词频分析。

- **年度人物与双向私聊排行**：按双方消息数统计，年度状元并列时会标注。请导入包含原始会话文件夹的电脑 ZIP；只有能用会话文件夹和所选档案中的发送者账号核对的私聊才计入排行。单个聊天 JSON 仍可用于常用字词统计。已识别的重复消息按会话标识和消息 ID 去重。
- **常用字词与本人表情**：在「范围与来源」输入档案中的 `sender_username`，或先生成一次统计，再从已统计的发送者选择本人账号。中文用 Jieba 词典离线分词；微信表情统计已知的方括号名称，例如 `[呲牙]`。
- **朋友圈发帖排行**：按发布者账号比较同一范围内的动态数。同一动态 ID 计一次；缺少发布者账号的动态和手机收藏不计入排行。

## 从手机收藏

朋友圈页点「＋ 收藏」可保存文字、链接和所选图片。来源应用提供系统分享时，也可以在分享菜单里选拾聊。原发布时间可手填，留空则记录为未知，JSON 中另存实际收藏时间。

Android 应用只能读取用户主动提供的文件或分享。微信的私有数据库由系统隔离，微信里可见的动态范围取决于客户端自身。拾聊在本机保存档案与图片，应用没有联网权限。点击分享链接会调用系统浏览器。

## 重新构建与验收

需要 Windows 上的 PowerShell 7、JDK 17 或更新版本、Python 3 和 Android SDK 的 `platforms;android-35`、`build-tools;35.0.0`。构建将固定版本的 Jieba 词典编译成 APK 内的离线词典，许可证也随包附带。预览包需要自己的 APK 签名密钥。同一个包名使用相同签名可覆盖安装。

```powershell
python -m pip install jieba==0.42.1
$env:SHILIAO_APK_PASSWORD = Read-Host -MaskInput
keytool -genkeypair -storetype PKCS12 -keystore .\preview.p12 `
  -storepass:env SHILIAO_APK_PASSWORD -alias shiliao-preview `
  -keyalg RSA -keysize 2048 -validity 3650 -dname "CN=Shiliao Android Preview"
.\android\build.ps1 -Sdk C:\Android\Sdk -Keystore .\preview.p12
```

`build.ps1 -Instrumentation` 使用同一把签名密钥构建仪器测试 APK，需要指定另一个输出文件名。先安装普通 APK，再将测试 APK 安装到独立测试设备或模拟器，运行下列命令。

```powershell
adb shell am instrument -w io.github.whycannotihaveaname.shiliao.test/io.github.whycannotihaveaname.shiliao.SelfTest
```

本验收版在 Android 15（API 35）模拟器安装验证。自动测试覆盖导入、多格式导出、评论开关与回复跳转、照片完整性、时间范围、年度人物、字词表情、发帖排行和取消统计。界面验收使用合成档案。
