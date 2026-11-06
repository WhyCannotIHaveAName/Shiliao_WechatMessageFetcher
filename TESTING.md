# Windows 验证

在 Windows x64、微信 4.2.0.11 上测试。自动测试使用虚构消息，覆盖跨分片顺序及发送者、超过 100 条的导出、日期筛选、嵌套转发、非标准 XML 声明、特殊消息、缩略图和缺图处理、取消及 WAL 快照。HTML、TXT 和 JSON 均参与测试。

运行 python test_export.py 检查源码。发行包内的 packaged-test.json 记录可执行文件自检结果。Android 和鸿蒙尚未适配。
