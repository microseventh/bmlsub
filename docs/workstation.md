# Workstation 工作流

Workstation 把一个单集分为三个阶段：预处理、人工交接、本地生产与发布。

## `ws start`

`ws start` 负责：

1. 解析番组根目录和数字单集。
2. 创建或验证 `bgminfo/series.json`。
3. 登记源视频并检查输入身份。
4. 提取所有匹配的文本参考字幕。
5. 提取归档音频和 Whisper 用 WAV。
6. 按 `none`、`quick` 或 `full` 策略运行 direct/chunked 转录。

完成后停止在人工翻译、校对和字体收集边界。正式字幕或字体缺失时，`ws end` 会返回 `needs_review`，不会开始压制。

## 人工交接

在单集目录准备：

- 正式简日 ASS：`<episode>.chs&jpn.ass`
- Aegisub 使用的顶层字体文件
- 已完成并校对的字幕事件和样式

程序会检查正式字幕和字体数量，并将它们登记为当前生产输入。参考字幕不作为正式生产字幕。

## `ws end`

`ws end` 的本地阶段按以下顺序执行：

1. 保存 CHS 字幕快照。
2. 生成并验证 CHT 字幕。
3. 验证字幕与字体。
4. 编码 HEVC 10-bit。
5. 编码 CHS/CHT H.264 内嵌 MP4。
6. 封装简繁内封 MKV。
7. 创建所选产品的 Torrent。

随后进入文件交付配置和外部发布。每一步都写入 `workstation/state/steps/`，成功结果在下次运行复用。

## 阶段判断

| 阶段 | 含义 | 下一步 |
| --- | --- | --- |
| `preprocess` | 源视频已登记，预处理未完成 | 继续 `ws start` |
| `human_handoff` | 预处理完成，缺正式字幕或字体 | 人工准备输入 |
| `local_production` | 正式字幕和字体就绪 | `ws end` |
| `publish` | 本地产品和 Torrent 就绪 | 配置并确认外部交付 |
| `complete` | 发布回执完整 | 检查总结和归档 |

状态判断以当前 Manifest、SQLite Artifact 和文件系统三者一致为准；历史失败记录不会覆盖当前成功输入。
