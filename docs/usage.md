# 使用指南

## 安装和初始化

```bash
conda activate base
cd /Users/miwata/Movies/BML/Project/bmlsub
pip install -e ".[all]"
```

安装后可运行 `bmlsub --help`。只使用 Python API 时也需要安装运行阶段涉及的依赖。

## 当前目录项目配置

在项目根目录执行一次：

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
```

命令会在当前目录生成 `bmlsub-project.json`。该文件只保存非敏感字段，不保存 R2 密钥、qB 密码或发布 token。

```bash
bmlsub config show
bmlsub config update --episodes 01-13 --notes "修订备注"
```

读取优先级为：显式命令参数 > `bmlsub-project.json` > 程序默认值。配置中只有一个集号时，单集命令可省略 `--episode-id`；配置中有多集时仍需明确指定。没有配置文件时，所有命令保持原有行为。

## 单集流程

建议先检查和规划，再运行有副作用的阶段：

```bash
bmlsub episode inspect --episode-dir /path/to/01 --episode-id 01
bmlsub episode plan --episode-dir /path/to/01 --episode-id 01
```

显式输入文件名不必以集号开头，但应同时传入 `--episode-id`：

```bash
bmlsub episode plan \
  --episode-dir /path/to/01 \
  --episode-id 01 \
  --source-video /path/to/01/raw_source_v2.mkv \
  --chs-subtitle /path/to/01/custom_chs.ass \
  --cht-subtitle /path/to/01/custom_cht.ass
```

### 一条龙调用

仅运行本地阶段：

```bash
bmlsub episode run \
  --episode-dir /path/to/01 \
  --episode-id 01 \
  --name-chs 作品名 \
  --name-cht 作品名 \
  --romaji Romaji \
  --local-only
```

全流程可去掉 `--local-only`，按需提供 `--r2-prefix` 和 `--qb-host`。上传和做种会访问外部服务，建议先分别检查配置。

可用的阶段跳过参数：

- `--skip-transcribe`
- `--skip-encode`
- `--skip-package`
- `--skip-upload`
- `--skip-seed`

### 细粒度命令链

```bash
bmlsub episode audio --episode-dir /path/to/01 --episode-id 01
bmlsub episode subs --episode-dir /path/to/01 --episode-id 01 --smart
bmlsub episode transcribe --episode-dir /path/to/01 --episode-id 01
bmlsub episode encode --episode-dir /path/to/01 --episode-id 01
bmlsub episode validate --episode-dir /path/to/01 --episode-id 01
bmlsub episode package --episode-dir /path/to/01 --episode-id 01
```

需要从简体生成繁体时：

```bash
bmlsub episode validate \
  --episode-dir /path/to/01 \
  --episode-id 01 \
  --ensure-cht
```

默认只把识别为中文的 Dialogue 可见文本提交给繁化姬；ASS 头部、样式、字体、日文、Comment、覆盖标签和矢量绘图保持不变。若 ASS 无法解析或有待繁化内容却无法可靠感知，会自动将完整文件交给繁化姬作为保底。使用 `--full-file-hanvert` 可直接跳过 ASS 分析，使用 `--no-full-file-fallback` 可禁用保底并让异常显式失败。已有繁体字幕会在新文件完整生成后才备份和原子替换，转换失败时保持原文件不动。

### 字幕文本分析

将指定 ASS 的中文、日文、混合文本及统计导出为 JSON：

```bash
bmlsub episode analyze-ass --ass-file /path/to/01.chs&jpn.ass
```

默认生成 `/path/to/01.chs&jpn.analysis.json`。分析会去除覆盖标签，将 `\N` / `\n` 变成实际换行、`\h` 变成空格，并排除 `\p` 绘图坐标。每条记录仍保留原始 Text、行号、时间和样式，便于回查。

Python 中可复用同一套清洗逻辑：

```python
from bmlsub import extract_ass_analysis, strip_ass_tags

plain = strip_ass_tags(r"这{\b1}里面{\b0}有东西")
data = extract_ass_analysis("01.chs&jpn.ass", "01.analysis.json")
```

## 合集 / Workstation

合集目录通常包含 `RAW`、`CHS&JPN`、`CHT&JPN`。集号支持范围和逗号列表：

```bash
bmlsub workstation inspect --root-dir /path/to/project --episodes 01-12
bmlsub workstation plan --root-dir /path/to/project --episodes 01,03,05
```

合集命令统一放在 `workstation` 分组下：

```bash
bmlsub workstation inspect --root-dir /path/to/project --episodes 01-12
bmlsub workstation plan --root-dir /path/to/project --episodes 01-12
bmlsub workstation validate --root-dir /path/to/project --episodes 01-12
bmlsub workstation encode --root-dir /path/to/project --episodes 01-12
bmlsub workstation release --root-dir /path/to/project --episodes 01-12
```

不传 `--episodes` 时，`WorkstationConfig` 会从 `RAW/*.mkv` 推断纯数字集号。

`workstation encode` 当前返回批量输入/输出规划，不直接执行批量编码；`workstation release` 返回发布目录和 torrent 计划，不直接创建 torrent。

## Python API

### 单集

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, ProjectNaming

work_dir = Path("/path/to/01")
project = ProjectNaming(
    group="Billion Meta Lab",
    name_chs="作品名",
    name_cht="作品名",
    romaji="Romaji",
)
pipe = Pipeline(PipelineConfig(work_dir=work_dir, project=project))

print(pipe.inspect_episode(work_dir, episode_id="01"))
print(pipe.plan_episode(work_dir, episode_id="01").summary())

result = pipe.process_episode(
    episode_dir=work_dir,
    episode_id="01",
    source_video=work_dir / "raw_source_v2.mkv",
    chs_subtitle=work_dir / "custom_chs.ass",
    cht_subtitle=work_dir / "custom_cht.ass",
    project=project,
    skip_upload=True,
    skip_seed=True,
)
print(result)
```

### 合集

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, WorkstationConfig

root = Path("/path/to/project")
pipe = Pipeline(PipelineConfig(work_dir=root))
ws = WorkstationConfig(
    root_dir=root,
    episode_ids="01-12",
    group="Billion Meta Lab",
    name_chs="作品名",
    name_cht="作品名",
    romaji="Romaji",
    r2_prefix="作品名/season1",
)

print(pipe.inspect_workstation(ws).summary())
print(pipe.plan_workstation(ws).summary())
```

## 输出约定

CLI 的最终结果统一序列化为 JSON 并写入 stdout；底层流水线自身的进度和提示重定向到 stderr。因此可以安全地使用：

```bash
bmlsub episode inspect --episode-dir /path/to/01 --episode-id 01 | jq .episode_id
```
