# 开发与测试

## 环境

```bash
conda create -n bmlsub-test python=3.12
conda activate bmlsub-test
python -m pip install -e .
```

开发和回归测试使用独立的 `bmlsub-test` 环境，不应修改日常生产使用的
`bmlsub` 环境。仅在需要实际运行 MLX 转录时另外安装 `.[transcription]`。

项目要求 Python 3.10+；开发环境推荐 Python 3.12。媒体测试需要 `ffmpeg`、`ffprobe` 和 `mkvmerge`。

## 常用检查

```bash
python -m unittest discover -s tests -v
python -m compileall bmlsub
git diff --check
bmlsub --help
bmlsub --version
python -m build
unzip -l dist/*.whl | grep -Ei 'SubsRefine|license|notice'
tar -tzf dist/*.tar.gz | grep -Ei 'SubsRefine|license|notice'
```

测试应覆盖 CLI 拒绝规则、Workstation 阶段判断、Artifact 漂移、回执复用、凭据安全边界、平台锁和外部 adapter 的模拟响应。不要在自动化测试中使用真实 Token、真实发布目录或真实外部发布接口。

## 代码边界

- `bmlsub/cli.py`：紧凑公开 CLI 和交互入口。
- `bmlsub/editing.py`：`build editsub` 的路径解析、固定后处理、验证和原子输出。
- `bmlsub/traditionalization.py`：`build fanhua` 的批量发现、ASS 验证和原子输出。
- `bmlsub/_vendor/subsrefine/`：固定提交的 SubsRefine MIT 许可处理核心。
- `bmlsub/workstation/`：Workstation 阶段、问题菜单、计划和状态。
- `bmlsub/workstation/operations.py`：独立操作注册表。
- `bmlsub/credentials/`：清单模型和系统 Secret Store。
- `bmlsub/release/`：R2、VPS、qBittorrent、Anibt adapter。
- `bmlsub/state/`：SQLite 作业和 Artifact 状态。

新增外部动作时必须增加输入身份、回执验证、失败恢复和无 Secret 日志测试。不要通过 CLI 重新暴露已经移除的旧业务 flag。

修改 vendored 第三方代码时，必须同步核对来源提交、完整许可文件、
`THIRD_PARTY_NOTICES.md`，并确认 wheel 和 sdist 仍包含第三方许可。
