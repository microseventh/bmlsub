# pyjs 代码审计报告

**审计日期**: 2026-07-10
**审计范围**: `bmlsub/` 全部 12 个源文件 + 1 个 Jupyter Notebook
**审计方法**: 静态源码分析 + Notebook 运行时输出验证

---

## 问题总览

| 严重度 | 数量 | 说明 |
|--------|------|------|
| 🔴 Critical Bug | 3 | 导致数据丢失、虚假成功反馈 |
| 🟡 Bug | 7 | 逻辑错误、崩溃风险、资源泄漏 |
| 🟠 设计不一致 | 4 | SSH/qB/哈希实现碎片化 |
| 🔵 缺失功能 | 8 | 无 dry-run、无超时、无日志框架 |
| ⚪ 代码质量 | 11 | 反模式、死代码、类型不一致 |

---

## 🔴 Critical Bugs

### #1 — Seeder.\_parse\_add\_response: SSH 横幅污染导致假失败

- **文件**: [bmlsub/seeder.py:364-385](bmlsub/seeder.py#L364-L385)
- **严重度**: Critical
- **分类**: Bug

**问题描述**: 当通过 SSH 执行 `curl` 时，远程服务器的 MOTD/登录横幅会被混入 stdout。`_parse_add_response` 使用 `raw.startswith("{")` 判断 JSON 响应，但此时 `raw` 的实际内容为：

```
Welcome to Ubuntu 22.04.3 LTS...
{"added_torrent_ids":["7ce1ae05..."],"failure_count":0,"pending_count":0,"success_count":1}
```

`startswith("{")` 返回 `False`，`== "Ok."` 也不匹配，函数返回 `False`。

**Notebook 实测证据** (cell b79e807c):

```
⚠️ 添加失败 [...] : {"added_torrent_ids":["7ce1ae05..."],...,"success_count":1}
✅ 做种完成: 0/3 成功
```

全部 3 个种子实际添加成功（`success_count: 1`），但报告为 `0/3 成功`。

**修复建议**:

```python
# 方案 A: SSH 命令加 -q 静默模式
subprocess.run(["ssh", "-q", ssh_alias, cmd], ...)

# 方案 B: 用正则提取 JSON 部分
import re
match = re.search(r'\{.*\}', raw, re.DOTALL)
if match:
    raw_json = match.group()
    # 再解析...
```

---

### #2 — R2Uploader.sync\_to\_server: 未校验即删除 R2 文件（数据丢失风险）

- **文件**: [bmlsub/r2upload.py:235-243](bmlsub/r2upload.py#L235-L243)
- **严重度**: Critical
- **分类**: Bug

**问题描述**: `sync_to_server` 的 SHA-256 哈希记录 (`self._hashes`) 是实例变量，仅在同一个 `R2Uploader` 实例的 `upload_file` 调用中被填充。当用户在**新的 notebook cell 或新的 Python 进程**中创建新的 `R2Uploader` 实例来调用 `sync_to_server`，`_hashes` 为空字典，所有文件都走：

```python
if not local_hash:
    print(f"  ⚠️ {filename}: 无本地哈希记录，跳过")
    continue  # ← 跳过校验，但不设置 all_ok = False
```

由于跳过的校验**没有把 `all_ok` 设为 `False`**，最终进入：

```python
if delete_after and all_ok:  # all_ok 仍为 True
    for key in r2_files:
        self.delete_remote(key)  # ←  未校验就删除！
```

**Notebook 实测证据** (cell 00487347):

```
⚠️ [6个文件]: 无本地哈希记录，跳过
🗑️  清理 R2 (6 个文件)...
  已删除: [...6个文件全部被删...]
```

**修复建议**:

```python
# 方案 A: 跳过的校验视为失败
if not local_hash:
    print(f"  ⚠️ {filename}: 无本地哈希记录，跳过")
    all_ok = False  # ← 添加这行
    continue

# 方案 B: 在 sync_to_server 入口处检查
if not self._hashes:
    raise R2UploadError("无本地哈希记录，无法校验，拒绝删除 R2 文件。"
                        "请在同一 R2Uploader 实例上先调用 upload_files()，"
                        "或设置 delete_after=False。")
```

---

### #3 — TorrentCreator.create: v1\_only 参数缺失（可能已修复）

- **文件**: [bmlsub/torrent.py:99](bmlsub/torrent.py#L99)
- **严重度**: Critical
- **分类**: Bug

**问题描述**: Notebook 运行时报错：

```
TypeError: TorrentCreator.create() got an unexpected keyword argument 'v1_only'
```

当前源码已包含 `v1_only: bool = False` 参数，说明可能是一个 `.pyc` 缓存问题——旧版本的字节码残留。

**修复建议**: 清理 `__pycache__` 目录，确保字节码与源码一致。长期建议添加 CI 或版本校验机制。

```bash
find . -name '__pycache__' -type d -exec rm -rf {} +
```

---

## 🟡 Bugs

### #4 — MediaExtractor.extract\_preferred\_subtitles: langs 参数被静默忽略

- **文件**: [bmlsub/media.py:199-224](bmlsub/media.py#L199-L224)
- **严重度**: Medium
- **分类**: 逻辑错误

**问题描述**: 方法接受 `langs: list[str] | None = None` 参数并赋值默认值，但实际语言过滤逻辑在第 224 行硬编码为 `["chi", "eng", "jpn"]`。传入自定义 `langs=["chi", "jpn"]` 仍会提取英文字幕。

```python
if langs is None:
    langs = ["chi", "eng", "jpn"]  # ← 赋值给了 langs...

# ... 

non_empty = [k for k in ["chi", "eng", "jpn"] if buckets[k]]  # ← 但这里硬编码
```

**修复建议**: 将 `non_empty` 行的硬编码替换为 `langs` 变量。

---

### #5 — Transcriber.\_merge\_chunks: 正则匹配失败导致 AttributeError 崩溃

- **文件**: [bmlsub/transcribe.py:240-244](bmlsub/transcribe.py#L240-L244)
- **严重度**: Medium
- **分类**: 崩溃风险

**问题描述**:

```python
txt_files = sorted(
    p_dir.glob("output_*.txt"),
    key=lambda f: int(re.search(r"output_(\d+)", f.name).group(1)))
```

如果文件名匹配 `output_*.txt` glob 但不匹配正则（如 `output_abc.txt`、`output_.txt`），`re.search()` 返回 `None`，`.group(1)` 抛出 `AttributeError`，导致整个分割转录在合并阶段崩溃——前面所有切片转录结果全部白费。

**修复建议**:

```python
def _safe_sort_key(f):
    m = re.search(r"output_(\d+)", f.name)
    return int(m.group(1)) if m else -1
txt_files = sorted(p_dir.glob("output_*.txt"), key=_safe_sort_key)
```

---

### #6 — Transfer.\_croc\_send: 超时后子进程孤儿

- **文件**: [bmlsub/transfer.py:254-295](bmlsub/transfer.py#L254-L295)
- **严重度**: Medium
- **分类**: 资源泄漏

**问题描述**: 15 秒超时后如果未找到 croc 暗号，函数返回 `(proc, master_fd, None)`。调用方 `send_files` 抛出 `CrocTransferError`，但 `proc`（正在运行的 croc 进程）从未被 terminate/kill。croc 进程持续在后台运行。

**修复建议**: 在超时或异常路径中调用 `proc.terminate()` 并 `proc.wait()`。

---

### #7 — Transfer.\_croc\_receive\_remote: 无超时无限循环 + 竞态条件

- **文件**: [bmlsub/transfer.py:307](bmlsub/transfer.py#L307)
- **严重度**: Medium
- **分类**: 挂起风险

**问题描述**:

```python
while not channel.exit_status_ready() or channel.recv_ready():
    ...
    time.sleep(0.1)
```

1. **无超时**: 如果远程 croc 挂死或网络断开，循环永远不退出
2. **竞态**: `not channel.exit_status_ready() or channel.recv_ready()` — 如果 exit_status 在两次检查之间变为 ready 但还有缓冲数据，循环会提前退出

**修复建议**: 添加超时机制；将条件改为 `while not channel.exit_status_ready():` + 内部超时。

---

### #8 — Encoder.strip\_metadata: tmp.replace() 异常未被捕获

- **文件**: [bmlsub/encode.py:165-181](bmlsub/encode.py#L165-L181)
- **严重度**: Medium
- **分类**: 未处理异常

**问题描述**: `tmp.replace(video_path)` 可能因为权限错误、跨文件系统等原因失败，但这个异常在 `except subprocess.CalledProcessError` 块之外，会直接向上传播。调用方如果没有准备，可能导致流程中断。

**修复建议**: 将 `tmp.replace()` 纳入 try 块或添加独立的异常处理。

---

### #9 — MediaExtractor.\_bundle\_preferred: 死代码

- **文件**: [bmlsub/media.py:352](bmlsub/media.py#L352)
- **严重度**: Low
- **分类**: 死代码

```python
extractor = MediaExtractor.__new__(MediaExtractor)  # 仅用于调用静态方法
```

变量 `extractor` 创建后从未被使用。实际调用 `_classify_lang_raw` 时使用的是类方法调用 `MediaExtractor._classify_lang_raw(lang)`（行 357）。`__new__` 创建的裸实例是多余的。

**修复建议**: 删除该行。

---

### #10 — TorrentCreator.create: 文件与目录分支完全相同

- **文件**: [bmlsub/torrent.py:124-127](bmlsub/torrent.py#L124-L127)
- **严重度**: Low
- **分类**: 死代码 / copy-paste 错误

```python
if src.is_file():
    dst = src.parent / f"{src.name}.torrent"
else:
    dst = src.parent / f"{src.name}.torrent"  # ← 完全相同的逻辑
```

**修复建议**: 移除分支，或确认是否需要不同逻辑（如目录时用 `src.name + ".torrent"` 还是其他命名规则）。

---

## 🟠 设计不一致

### #11 — 三种不同的 SSH 实现

| 模块 | 方式 | 配置 |
|------|------|------|
| `Transfer` | `paramiko.SSHClient` + `RSAKey` | `dict: {host, port, user, key_path}` |
| `RemoteSeeder` | `subprocess.run(["ssh", alias, cmd])` | `str: ssh_alias` |
| `R2Uploader` | `subprocess.run(["ssh", alias, cmd])` | `str: ssh_alias` |

`RemoteSeeder._ssh_run` 和 `R2Uploader._ssh_run` 是两个独立的实现，功能几乎相同。

**建议**: 抽取统一的 SSH 工具模块，支持 paramiko（代码内嵌）和 subprocess ssh（依赖 `~/.ssh/config`）两种模式。

---

### #12 — 两种不同的 qBittorrent 操控方式

| 类 | 方式 | 适用场景 |
|----|------|----------|
| `Publisher.seed_qbittorrent` | `qbittorrentapi` Python 库直连 | qB Web UI 可直接访问 |
| `RemoteSeeder` | SSH + curl | qB 藏在防火墙/Docker 后面 |

`Pipeline.process_episode` 的 `seed_torrents` 方法只使用 `Publisher.seed_qbittorrent`，在 Docker 部署场景下不可用。

**建议**: Pipeline 应同时支持两种方式，或优先使用 `RemoteSeeder`（覆盖率更广）。

---

### #13 — SHA-256 实现重复

| 位置 | Buffer Size |
|------|------------|
| `Transfer._sha256_local` (transfer.py:178) | 4096 bytes |
| `R2Uploader._sha256_local` (r2upload.py:369) | 8192 bytes |

完全相同的逻辑，仅 buffer 大小不同。

**建议**: 抽取为 `bmlsub/_hash_utils.py` 或直接放在 `__init__.py` 中。

---

### #14 — Pipeline transfer 和 seed 步骤不兼容

`process_episode` 的 stage 6（transfer）通过 croc + paramiko SSH 传输文件。stage 7（seed）通过 `Publisher.seed_qbittorrent` 直连 qB API。如果 qB 运行在 Docker 容器中（仅通过 SSH 隧道可达），step 7 必然失败。

**建议**: 合并为统一的远程操作模式，或让 seed 步骤也走 SSH 通道（使用 `RemoteSeeder`）。

---

## 🔵 缺失功能

| # | 描述 | 影响 |
|---|------|------|
| 15 | **无 dry-run / preview 模式** | 无法预览将执行的命令，所有操作立即执行 |
| 16 | **分割转录无断点续传** | 转录处理到一半崩溃，只能从头开始 |
| 17 | **PipelineConfig 无参数校验** | 负数 `chunk_sec`、空 `language` 等静默接受 |
| 18 | **无日志框架** | 全项目用 `print()` + emoji，无法控制输出级别或写入文件 |
| 19 | **subprocess 无超时** | `encode.py`, `media.py`, `package.py` 中所有 `subprocess.run()` 均无 `timeout` 参数。ffmpeg/mkvmerge 卡死则 Python 进程永久挂起 |
| 20 | **rclone remote 名硬编码** | `sync_to_server` 中 `f"r2:{bucket}"` 硬编码 remote 名为 `"r2"`，不灵活 |
| 21 | **\_hashes 不可持久化** | `R2Uploader._hashes` 是实例内字典，跨 cell/进程丢失 |
| 22 | **model\_short\_name 仅处理两种前缀** | 其他 HuggingFace 模型名（如 `openai/whisper-base`）无法正确简写 |

---

## ⚪ 代码质量

| # | 文件 | 行号 | 问题描述 |
|---|------|------|----------|
| 23 | [encode.py](bmlsub/encode.py#L198) | 198 | `__import__('json')` 反模式，应改为顶部 `import json` |
| 24 | [transfer.py](bmlsub/transfer.py#L133) | 133 | `'local_archive' in dir()` 脆弱的变量名检测清理模式 |
| 25 | [package.py](bmlsub/package.py#L284-L291) | 284-291 | 字幕语言检测用 `"chs" in lower` 子串匹配，可能误判 |
| 26 | [config.py](bmlsub/config.py#L107) | 107 | `output_transcripts_dir` 类型为 `str`，与 `work_dir: Path` 不一致 |
| 27 | [config.py](bmlsub/config.py#L74) | 74 | `work_dir` 在配置创建时 resolve，cwd 变化后过期 |
| 28 | [config.py](bmlsub/config.py#L40) | 40 | `extra_params` 仅在 x264 分支追加，HEVC 分支不生效（无文档说明） |
| 29 | [subtitle.py](bmlsub/subtitle.py#L118-L119) | 118-119 | `standardize_ass` 原地覆盖无备份，正则替换可能损坏文件 |
| 30 | [transcribe.py](bmlsub/transcribe.py#L58) | 58 | `output_path` 类型标注 `Path\|None` 但实际接受 `str` |

---

## Notebook 集成问题

| # | 阶段 | 问题 | 根因 |
|---|------|------|------|
| N1 | 7 (种子) | `v1_only` 参数报错 | #3 — 字节码/源码不同步 |
| N2 | 11 (做种) | 3/3 成功但报告 0/3 | #1 — SSH 横幅污染 |
| N3 | 10 (R2 拉取) | 6 文件被删但零校验通过 | #2 — 哈希记录丢失 + all_ok 未标记 |

---

## 修复优先级建议

```
第一轮（防止数据丢失 + 虚假反馈）── 本周内
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔴 #2  R2 sync_to_server: 跳过校验时设 all_ok=False
  🔴 #1  Seeder SSH 横幅: 正则提取 JSON 段
  🟡 #7  croc receive: 加超时 + 修复竞态条件

第二轮（健壮性）── 二周内
━━━━━━━━━━━━━━━━━━━━━
  🟡 #5  merge_chunks 正则崩溃 → 安全 fallback
  🟡 #6  croc 子进程孤儿 → finally terminate
  🟡 #19 所有 subprocess.run 加 timeout
  🔴 #3  清理 __pycache__，确认 v1_only 修复

第三轮（架构统一 + 新功能）── 一月内
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🟠 #11 统一 SSH 工具模块
  🟠 #12 Pipeline 支持 RemoteSeeder 模式
  🟠 #13 合并 SHA-256 实现
  🔵 #21 _hashes 持久化 / 支持 recalculate
  🔵 #18 引入 logging 框架
  🔵 #19 subprocess 超时
  🟡 #4  修复或移除无用的 langs 参数
  ⚪ #23-30 代码清理
```

---

## 附录：审计覆盖的源文件

```
bmlsub/
├── __init__.py        ✅ 已审计
├── config.py          ✅ 已审计
├── media.py           ✅ 已审计
├── encode.py          ✅ 已审计
├── transcribe.py      ✅ 已审计
├── subtitle.py        ✅ 已审计
├── package.py         ✅ 已审计
├── torrent.py         ✅ 已审计
├── transfer.py        ✅ 已审计
├── r2upload.py        ✅ 已审计
├── seeder.py          ✅ 已审计
├── publish.py         ✅ 已审计
├── pipeline.py        ✅ 已审计
└── README.md          ✅ 已审计（新建，不在审计范围）
```
