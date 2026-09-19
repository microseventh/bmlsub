# bmlsub 1.3.2

[GitHub](https://github.com/microseventh/bmlsub) |
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
bmlsub build fanhua [file-or-directory]
bmlsub build editsub [file-or-directory]
bmlsub rebuild [option]
```

- `ws start` initializes a series, registers source media, extracts reference subtitles and audio, and optionally runs transcription before the human subtitle handoff.
- `ws end` starts from the completed subtitles and fonts, produces local releases, and delivers them through R2, VPS, qBittorrent, and Anibt in order.
- `ws end yes` resumes delivery unattended with saved, validated configuration and automatically confirms Nyaa syndication.
- `build` runs one standalone operation in the current directory.
- `build fanhua` converts one ASS file or a non-recursive directory of ASS
  files from Simplified to Traditional Chinese while preserving ASS structure.
- `build editsub` converts one ASS/SRT/VTT subtitle or a non-recursive
  directory of subtitles into validated UTF-8 Japanese text.
- `rebuild` replaces the result of one standalone operation; `rebuild anibt` is refused because publication cannot be safely overwritten.

The public global options are `-h/--help` and `--version`. The two subtitle
file operations intentionally accept one optional file-or-directory path;
other standalone paths, recipes, output locations, and credential references
are selected through interactive questions rather than business flags.

## Installation

Python 3.10 or newer is required. MLX transcription on Apple Silicon is an
optional feature. Install the native dependencies first, then install bmlsub
directly from GitHub:

```bash
conda create -n bmlsub python=3.12
conda activate bmlsub
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
brew install mkvtoolnix
python -m pip install "git+https://github.com/microseventh/bmlsub.git"
bmlsub --version
```

The expected version output is `bmlsub 1.3.2`.

To install the optional MLX Whisper integration from GitHub on Apple Silicon:

```bash
python -m pip install "bmlsub[transcription] @ git+https://github.com/microseventh/bmlsub.git"
```

For a versioned installation from verified GitHub Release assets, follow the
[Release installation guide](docs/release-installation.md).

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

## Subtitle text editing

```bash
# Traditionalize one ASS subtitle or each source ASS in a directory.
bmlsub build fanhua 'episode.chs&jpn.ass'
bmlsub build fanhua /path/to/subtitles

# Process supported subtitles in the current directory.
bmlsub build editsub

# Process one file or one directory. Output still goes to the current directory.
bmlsub build editsub 'episode.ja[cc].srt'
bmlsub build editsub /path/to/subtitles
```

`fanhua` supports ASS input and writes each output beside its source using the
existing CHT naming rules, such as `episode.chs&jpn.ass` to
`episode.cht&jpn.ass`. It preserves ASS structure and only converts reliable
Chinese dialogue segments through the configured Fanhuaji provider.

`editsub` uses the bundled SubsRefine processing core, then removes all
Unicode punctuation, decorative symbols, and controls. Internal whitespace is
collapsed to the Japanese ideographic space (`U+3000`); blank lines and edge
spaces are removed. The JSON result reports the UTF-8 line count and all text
validation checks. Directory scans are stable, non-recursive, and limited to
ASS, SRT, and VTT. Relative inputs are resolved from the current directory and
outputs are written there as `<stem>_processed.txt`. Both operations reject a
batch before writing if two inputs would map to the same output.

[SubsRefine at the integrated revision](https://github.com/MingYSub/SubsRefine/tree/c47cec799fb5615d74a6561a7b6a91054c669c4b)
is Copyright (c) 2025 MingYSub and MIT-licensed. Its complete license is
distributed in `LICENSES/SubsRefine-MIT.txt`; integration details are in
`THIRD_PARTY_NOTICES.md`. Users remain responsible for the rights to the
subtitle content they process.

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
- [Install from a GitHub Release](docs/release-installation.md)
- [CLI reference](docs/cli.md)
- [Workstation workflow](docs/workstation.md)
- [Standalone operations and state](docs/operations.md)
- [Credentials and secure storage](docs/credentials.md)
- [Publishing and recovery](docs/publishing.md)
- [Development and tests](docs/development.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture overview](docs/architecture.md)
- [简体中文文档索引](docs/zh/README.md)
