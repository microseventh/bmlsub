# 开发与发布检查

[English](../development.md) · [文档首页](README.md)

## 环境

使用 Conda base，并以 editable 模式安装完整项目：

```bash
conda activate base
cd /path/to/bmlsub
python -m pip install -e .
```

## 源码检查

公开仓库不携带内部测试套件和私有验证媒体。发布前，维护者应在上传树之外运行内部检查，然后直接验证公开源码树：

```bash
python -m compileall -q bmlsub
bmlsub --version
bmlsub --help
bmlsub workstation series create --help
bmlsub workstation delivery --help
```

使用临时 workspace 驱动已安装 CLI 和公开 Python API。至少验证：番组配置创建、默认拒绝覆盖、显式替换、数字单集的番组发现、字幕转换与复用、Run 查询，以及 stdout 始终保持一个 JSON 文档。

## 打包检查

上传前构建并检查 wheel 与 sdist：

```bash
python -m build
python -m zipfile -l dist/*.whl
```

归档只应包含包源码和当前文档，不得包含 `.claude/`、`tools/`、构建缓存、本地数据库、凭证、媒体、回执或私有验证路径。随后在干净环境中安装构建出的 wheel，再执行基础 CLI smoke。

## 仓库清洁边界

`.gitignore` 排除 Python/build cache、本地 state/log/backups、credential/env/key、媒体、Torrent、receipt、analysis 和字体。发布前应扫描：

- `.DS_Store`、`__pycache__`、egg-info、build/dist 残留；
- SQLite/database/log 和生成媒体；
- private key block 或疑似凭证值；
- 本机绝对路径、私有主机 alias 和特定验证项目名；
- 超过 1 MB 的意外文件；
- 失效的 Markdown 相对链接。

新 Stage 继续复用 `StageRunner`、真实 `stage_inputs`、Artifact writer、argv-only `ProcessRunner` 和严格规范化 Profile。外部发布 smoke 应使用 fake client 或显式有界只读 probe；真实副作用必须由操作者另行确认。
