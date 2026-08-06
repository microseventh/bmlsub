# bmlsub 1.2.1

[简体中文](docs/zh/README.md)

`bmlsub` is a local workstation tool for subtitle, transcription, video
production, and release workflows. Each step is recorded as verifiable state
and receipts, while external network actions remain behind explicit
confirmation boundaries.

## Public entry points

```text
bmlsub ws start
bmlsub ws end [yes]
bmlsub build [option]
bmlsub rebuild [option]
```

- `ws start` initializes a series, registers source media, extracts reference subtitles and audio, and optionally runs transcription before the human subtitle handoff.
- `ws end` starts from the completed subtitles and fonts, produces local releases, and delivers them through R2, VPS, qBittorrent, and Anibt in order.
- `ws end yes` resumes delivery unattended with saved, validated configuration and automatically confirms Nyaa syndication.
- `build` runs one standalone operation in the current directory.
- `rebuild` replaces the result of one standalone operation; `rebuild anibt` is refused because publication cannot be safely overwritten.

The public global options are `-h/--help` and `--version`. Paths, inputs,
recipes, output locations, and credential references are selected through the
interactive questions rather than business flags.

## Installation

Python 3.10 or newer is required. MLX transcription on Apple Silicon is an
optional feature.

```bash
conda create -n bmlsub python=3.12
conda activate bmlsub
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
brew install mkvtoolnix
python -m pip install -e '.[transcription]'
bmlsub --version
```

The expected version output is `bmlsub 1.2.1`.

## Recommended workflow

Prepare a series root with this layout:

```text
Project/
  bgminfo/series.json
  01/
    01.mkv
```

Run `bmlsub ws start` from the series root. On the first run, the questions
can create `series.json`. After an episode directory exists, the command
registers the source video, extracts reference subtitles and audio, and runs
direct or chunked Whisper according to the selected policy.

After translation, proofreading, and font collection, place the formal ASS
subtitle and fonts in the episode directory, then run `bmlsub ws end`. The
delivery plan is shown before any R2, VPS, qBittorrent, or Anibt action and
requires confirmation.

## Standalone operations

| Option | Meaning |
| --- | --- |
| `bgminfo` | Create or validate series metadata |
| `ensub` | Extract English reference subtitles |
| `trans` | Extract audio and transcribe it |
| `pubinfo` | Configure publication metadata |
| `encode` | Encode and mux video products |
| `torrent` | Create a Torrent for existing content |
| `upr2` | Upload content to R2 |
| `dlvps` | Pull content to the VPS |
| `seed` | Verify content and seed it with qBittorrent |
| `anibt` | Publish a Torrent release to Anibt |

Each `build` command runs exactly one option. In a TTY, omitting the option
opens a menu; in a non-TTY it returns `needs_review` without initializing
state. `rebuild` moves the previous local target to `.bmlsub/backups/`,
validates the replacement, and only then writes the new receipt.

See [standalone operations and state](docs/operations.md) for the complete
input, output, and recovery rules.

## State and security

Standalone operation state is stored in `.bmlsub/build/`; Workstation state is
stored in `workstation/state/` inside the episode. Plans are immutable, and
receipts record file identities, parameters, and upstream relationships.

Passwords, tokens, access keys, and private keys are never written to
`series.json`, plans, receipts, or ordinary output. R2, qBittorrent, and Anibt
secrets use the system secure store; SSH configuration stores only an
OpenSSH host alias.

## Documentation

- [Documentation index](docs/README.md)
- [Quick start](docs/quickstart.md)
- [CLI reference](docs/cli.md)
- [Workstation workflow](docs/workstation.md)
- [Standalone operations and state](docs/operations.md)
- [Credentials and secure storage](docs/credentials.md)
- [Publishing and recovery](docs/publishing.md)
- [Development and tests](docs/development.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture overview](docs/architecture.md)
- [简体中文文档索引](docs/zh/README.md)
