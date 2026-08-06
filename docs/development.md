# 开发与测试

## 环境

```bash
conda activate bmlsub
python -m pip install -e '.[transcription]'
```

项目要求 Python 3.10+；开发环境推荐 Python 3.12。媒体测试需要 `ffmpeg`、`ffprobe` 和 `mkvmerge`。

## 常用检查

```bash
python -m unittest discover -s tests -v
python -m compileall bmlsub
git diff --check
bmlsub --help
bmlsub --version
```

测试应覆盖 CLI 拒绝规则、Workstation 阶段判断、Artifact 漂移、回执复用、凭据安全边界、平台锁和外部 adapter 的模拟响应。不要在自动化测试中使用真实 Token、真实发布目录或真实外部发布接口。

## 代码边界

- `bmlsub/cli.py`：紧凑公开 CLI 和交互入口。
- `bmlsub/workstation/`：Workstation 阶段、问题菜单、计划和状态。
- `bmlsub/workstation/operations.py`：独立操作注册表。
- `bmlsub/credentials/`：清单模型和系统 Secret Store。
- `bmlsub/release/`：R2、VPS、qBittorrent、Anibt adapter。
- `bmlsub/state/`：SQLite 作业和 Artifact 状态。

新增外部动作时必须增加输入身份、回执验证、失败恢复和无 Secret 日志测试。不要通过 CLI 重新暴露已经移除的旧业务 flag。
