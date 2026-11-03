# curseforge-cli

A dependency-free command-line client for the CurseForge API, built around Minecraft mod workflows: search the catalog, inspect projects and files, download artifacts, install mods with their required dependencies, update installed jars by fingerprint, and install modpacks.

The entire tool is a single Python file using only the standard library. No `pip install`, no virtualenv, no runtime dependencies.

## Features

- Full CurseForge API coverage: games, categories, mods, files, fingerprints, Minecraft versions and mod loaders
- `install` with recursive required-dependency resolution and a `--dry-run` preview
- `update` that identifies installed jars by CurseForge fingerprint and fetches newer compatible files
- Modpack installation from a local zip (`import`) or directly from CurseForge (`pack`)
- SHA1 checksum verification of downloads against API-provided hashes
- Automatic retry with backoff on `429` and common `5xx` responses
- Human-readable tables, plus `--json` output on list commands for scripting
- Cross-platform: Windows, Linux, macOS

## Requirements

- Python 3.8 or newer
- A CurseForge API key from [console.curseforge.com](https://console.curseforge.com/)

## Installation

```bash
git clone https://github.com/huack123/curseforge-cli.git
cd curseforge-cli
python cf.py --help
```

Optionally add the folder to your `PATH` to use the shorter commands:

- **Windows**: add `C:\path\to\curseforge-cli` to `PATH`, then use `cf` (via `cf.cmd`)
- **Linux/macOS**: `ln -s "$PWD/cf.py" ~/.local/bin/cf`

## Authentication

```bash
python cf.py auth <your-api-key>
```

The key is stored in:

- Windows: `%APPDATA%\cf-cli\config.json`
- Linux/macOS: `~/cf-cli/config.json`

The `CURSEFORGE_API_KEY` environment variable takes precedence over the saved key, which is useful for CI or temporary sessions.

## Usage

```bash
python cf.py <command> [options]
```

### Catalog

| Command | Description |
|---|---|
| `auth <key>` | Save the API key |
| `games` | List all games |
| `game [id]` | Game details (default `432`, Minecraft) |
| `game-versions [id] [--v2]` | Game versions, v1 or grouped v2 |
| `version-types [id]` | Version types (e.g. Minecraft release, snapshot) |
| `categories [--game] [--class-id]` | List categories |
| `search [query ...]` | Search projects |
| `mod <id\|slug>` | Project details |
| `mods <id>...` | Project details for several ids |
| `featured` | Featured, popular and recently updated projects (`--version-type`, `--exclude`, `--limit`, `--json`) |

`search` accepts `--version`, `--loader`, `--class-id`, `--category`, `--sort`, `--order`, `--limit`, `--page` and `--json`. Useful class ids for Minecraft: `6` mods, `17` worlds, `4471` modpacks.

### Files and downloads

| Command | Description |
|---|---|
| `files <mod>` | List a project's files |
| `file <mod> <file>` | Single file details |
| `file-info <fileId>...` | File details for several ids |
| `desc <mod>` | Project description (HTML) |
| `changelog <mod> <file>` | File changelog (HTML) |
| `url <mod> <file>` | Raw download URL |
| `download <mod> <file\|latest>` | Download one file |

`download` and `files` accept `--version` and `--loader`; `files` also supports `--release release|beta|alpha`.

### Installation and maintenance

| Command | Description |
|---|---|
| `install <mod>` | Install a mod and its required dependencies |
| `update` | Update installed jars via fingerprints |
| `import <pack.zip>` | Install a modpack from a local zip |
| `pack <id\|slug>` | Download and install a modpack directly |
| `scan <jar\|dir>` | Identify local jars by fingerprint |
| `worlds` | List local Minecraft worlds |

`install` and `update` target `%APPDATA%\.minecraft\mods` by default; override with `--dir`. `install --dry-run` prints the full plan without writing files. `update --dry-run` reports available updates only and never deletes old jars.

### Reference

| Command | Description |
|---|---|
| `loaders [--version]` | List Minecraft mod loaders |
| `loader <name>` | Loader details |
| `versions [--limit]` | List Minecraft versions |
| `version <version>` | Version details |

## Examples

```bash
# Find JEI for Fabric 1.21.1
python cf.py search jei --loader fabric --version 1.21.1

# Inspect the project and its files
python cf.py mod jei
python cf.py files 238222 --version 1.21.1 --loader fabric --release release

# Preview, then install with required dependencies
python cf.py install jei --version 1.21.1 --loader fabric --dry-run
python cf.py install jei --version 1.21.1 --loader fabric

# Identify every jar in a mods folder
python cf.py scan "C:\Users\<you>\AppData\Roaming\.minecraft\mods"

# Check for updates without downloading
python cf.py update --dry-run

# Install a modpack directly from CurseForge (find ids with: cf search --class-id 4471)
python cf.py pack all-the-mods-10 --dir D:\packs\atm10

# Export a raw API response for scripting
python cf.py search sodium --loader fabric --json
```

## How it works

- All requests go to `https://api.curseforge.com/v1` with the `x-api-key` header.
- The API returns at most 50 results per page and caps pagination at `index + pageSize <= 10,000`.
- Fingerprints use CurseForge's modified Murmur2 hash (seed `1`, with tab, LF, CR and space bytes stripped), matching the official implementation.
- Downloads are verified against the SHA1 hash supplied by the API; a mismatch deletes the partial file and aborts.
- `429` and `5xx` responses are retried up to four attempts, honoring `Retry-After` when present.
- Some projects disable third-party downloads; their files return no download URL and the command exits with a clear message.

## Testing

```bash
python -m unittest -v test_cf
```

The suite covers query construction, bulk request bodies, dependency resolution, modpack extraction, retry behavior, checksum failure handling and the fingerprint implementation.

## Project layout

```
cf.py        CLI implementation (stdlib only)
cf.cmd       Windows shim
test_cf.py   Unit tests
```

## Disclaimer

This is an unofficial client and is not affiliated with or endorsed by CurseForge or Overwolf. API usage is subject to the [CurseForge API terms](https://docs.curseforge.com/).
