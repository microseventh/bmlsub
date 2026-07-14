# CLI 文档

## 总览

```bash
bmlsub --help
bmlsub episode --help
bmlsub workstation --help
bmlsub episode ACTION --help
```

顶层只保留五个入口：`episode`、`workstation`、`upload`、`seed`、`config`。CLI 使用标准库 `argparse`，最终结构化结果以 JSON 输出到 stdout；进度信息与错误输出到 stderr。

## 项目配置

`config` 管理当前目录的 `bmlsub-project.json`：

```bash
bmlsub config init \
  --group "Billion Meta Lab" \
  --name-chs 作品名 \
  --name-cht 作品名 \
  --romaji Romaji \
  --episodes 01-12 \
  --r2-prefix 作品名/season1 \
  --bgm-id 123 \
  --notes "发布备注" \
  --qb-host http://qb:8080

bmlsub config show
bmlsub config update --episodes 01-13 --notes "修订备注"
```

- `config init` 默认拒绝覆盖已有文件；需要重建时使用 `--force`。
- 后续命令自动读取配置，显式参数优先覆盖。
- 只保存组名、作品名、集号和非敏感发布参数；不会保存密码、access key、secret 或 token。
- 配置中只有一集时，单集命令可省略 `--episode-id`；多集时必须显式指定。
- 没有配置文件时，命令继续使用原有参数和默认值。

## 公共配置参数

各子命令均可使用以下流水线配置：

- `--work-dir`：默认工作目录
- `--output-transcripts-dir`：转录输出目录
- `--language`：转录语言，默认 `ja`
- `--direct-model` / `--chunked-model`：转录模型覆盖
- `--chunk-sec` / `--overlap-sec`：分段转录设置
- `--group` / `--name-chs` / `--name-cht` / `--romaji`：项目命名

单集命令通常还接受：

- `--episode-dir`
- `--episode-id`
- `--source-video`
- `--chs-subtitle`
- `--cht-subtitle`

## 单集检查与计划

```bash
bmlsub episode inspect --episode-dir ./01 --episode-id 01
bmlsub episode plan --episode-dir ./01 --episode-id 01
```

可通过 `--prefix-chs`、`--prefix-cht` 覆盖最终产物前缀。

## 素材提取

```bash
bmlsub episode audio --episode-dir ./01 --episode-id 01
bmlsub episode subs --episode-dir ./01 --episode-id 01
bmlsub episode subs --episode-dir ./01 --episode-id 01 --smart
bmlsub episode media --episode-dir ./season --episodes 01-03
bmlsub episode media --episode-dir ./season --all-subs
```

- `episode subs --smart` 按配置的语言优先级筛选字幕。
- `episode media` 面向同一目录中的多集数字命名 MKV。

## 转录、编码与字幕

```bash
bmlsub episode transcribe --episode-dir ./01 --episode-id 01
bmlsub episode transcribe --episode-dir ./01 --episode-id 01 --manual-cut 01:30 --manual-cut 22:00
bmlsub episode encode --episode-dir ./01 --episode-id 01
bmlsub episode validate --episode-dir ./01 --episode-id 01
bmlsub episode validate --episode-dir ./01 --episode-id 01 --ensure-cht
```

字幕转换相关参数：

- `--converter`：繁化姬模式，如 `Taiwan`、`Traditional`、`Hongkong`
- `--conversion-api-url`
- `--conversion-timeout`
- `--full-file-hanvert`：跳过 ASS 分析，把完整文件直接提交给繁化姬
- `--no-full-file-fallback`：无法可靠感知时抛错，不自动全文件繁化
- `--regenerate-cht`
- `--keep-existing-cht`

默认只提交识别为中文的 Dialogue 可见文本，不提交 ASS 头部、样式、日文、Comment、标签或绘图数据。若 Events 无法解析或无法可靠形成转换任务，则保底改用完整文件繁化；`--full-file-hanvert` 可直接选择该模式。

### ASS 文本分析

```bash
bmlsub episode analyze-ass --ass-file ./01.chs&jpn.ass
bmlsub episode analyze-ass \
  --ass-file ./01.chs&jpn.ass \
  --output ./stats/01.json \
  --include-comments
```

默认输出 `<输入文件名>.analysis.json`。结果按 `zh`、`ja`、`mixed`、`other` 分组，每条事件保留行号、时间、样式、原始 Text 和去标签 Text；汇总包含语言事件数、字符数、标签、换行和绘图事件数。

## 封装

使用默认命名和产物规划：

```bash
bmlsub episode package \
  --episode-dir ./01 \
  --episode-id 01 \
  --name-chs 作品名 \
  --name-cht 作品名 \
  --romaji Romaji
```

也支持旧式模板参数 `--mkv-template`、`--chs-template`、`--cht-template`；三个模板必须同时提供才会采用旧式模板封装。

## 上传与做种

上传到 R2：

```bash
bmlsub upload ./release/a.mkv ./release/b.mp4 \
  --r2-prefix 作品名/01
```

凭证可以来自环境变量、配置文件或 `--r2-account-id`、`--r2-access-key-id`、`--r2-secret-access-key`、`--r2-bucket-name`、`--r2-endpoint`。

qBittorrent 做种：

```bash
bmlsub seed ./release/a.mkv \
  --qb-host http://127.0.0.1:8080 \
  --qb-user admin
```

这些命令有真实外部副作用；执行前请检查目标环境。

## 单集完整流程

`episode run` 是日常运行单集完整流程的入口：

```bash
bmlsub episode run --episode-dir ./01 --episode-id 01 --local-only
```

控制参数：

- `--local-only`：同时跳过上传和做种
- `--skip-transcribe`
- `--skip-encode`
- `--skip-package`
- `--skip-upload`
- `--skip-seed`
- `--r2-prefix`
- `--qb-host`

即使使用自定义源视频或字幕文件名，中间产物和最终产物仍以 `--episode-id` 命名。

## 合集命令

```bash
bmlsub workstation inspect --root-dir ./project --episodes 01-12
bmlsub workstation plan --root-dir ./project --episodes 01-12
bmlsub workstation validate --root-dir ./project --episodes 01-12
bmlsub workstation encode --root-dir ./project --episodes 01-12
bmlsub workstation release --root-dir ./project --episodes 01-12
```

合集目录名可通过以下参数覆盖：

- `--raw-dir-name`
- `--sub-dir-name`
- `--sub-tj-dir-name`
- `--hevc-subdir-name`

还可设置 `--r2-prefix`、`--bgm-id` 和 `--notes`，这些信息会进入合集配置与结果摘要。

## 旧命令兼容

旧的长命令仍可执行，例如 `bmlsub inspect-episode`、`bmlsub upload-r2` 和 `bmlsub plan-workstation`，但不会再显示在 `bmlsub --help` 中。新脚本和文档应统一使用分组命令。

旧式 `bmlsub episode --episode-dir ...` 也会自动按 `bmlsub episode run ...` 处理。

## 退出码

- `0`：命令调用完成；具体阶段是否 ready/ok 请查看 JSON 字段
- `1`：参数接线后的运行错误，如输入不存在或无法推断集号
- `2`：`argparse` 参数错误
