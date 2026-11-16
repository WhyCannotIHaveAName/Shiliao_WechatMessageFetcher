# 拾聊 Android 2.6.1 预览版

[下载签名 APK](https://github.com/WhyCannotIHaveAName/Shiliao_WechatMessageFetcher/releases/download/v2.6.1-android-preview.1/Shiliao-Android-2.6.1-preview.1.apk)，传到手机后在系统文件管理器中打开并安装。安装包声明支持 Android 8.0 及以上，测试环境见下文。

## 导入电脑档案

1. 用 Windows 拾聊完成一次导出。把**整个导出结果目录**压缩为 ZIP，保留 `朋友圈.json` 或各会话的 `聊天记录.json` 及同目录的 `assets`。把 ZIP 传到手机。
2. 打开拾聊，点「导入」并选择 ZIP。也可导入单个拾聊 JSON 来查看文字、时间和链接。照片需要随 ZIP 提供。
3. 切换「聊天记录」或「朋友圈」，从下拉列表选择档案。关键词和时间范围可叠加，点「导出 ZIP」保存筛选结果。ZIP 含 HTML、TXT、JSON 和随记录关联的可用附件。
4. 如果档案含 Windows 版导出的缓存图册，可在朋友圈页点「图册」查看。动态里的图片匹配状态沿用原档案。

## 从手机收藏

朋友圈页点「＋ 收藏」可保存文字、链接和所选图片。来源应用提供系统分享时，也可以在分享菜单里选拾聊。原发布时间可手填，留空则记录为未知，JSON 中另存实际收藏时间。

Android 应用只能读取用户主动提供的文件或分享。微信的私有数据库由系统隔离，微信里可见的动态范围取决于客户端自身。拾聊在本机保存档案与图片，应用没有联网权限。点击分享链接会调用系统浏览器。

## 重新构建与验收

需要 Windows 上的 PowerShell 7、JDK 17 或更新版本，以及 Android SDK 的 `platforms;android-35` 和 `build-tools;35.0.0`。预览包需要自己的 APK 签名密钥。同一个包名使用相同签名可覆盖安装。

```powershell
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

本预览在 Android 15（API 35）模拟器安装验证。自动测试覆盖多档案 ZIP、独立 JSON、中文与链接搜索、时间边界、照片字节在导出 ZIP 中的保留、收藏时间和坏档案回滚。界面验收使用合成档案，不含真实微信内容。
