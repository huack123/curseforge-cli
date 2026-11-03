# curseforge-cli

A dependency-free command-line client for the CurseForge API, built around Minecraft mod workflows: search the catalog, inspect projects and files, download artifacts, install mods with their required dependencies, update installed jars by fingerprint, and install modpacks.

The entire tool is a single Python file using only the standard library. No `pip install`, no virtualenv, no runtime dependencies.

## Features

- Full CurseForge API coverage: games, categories, mods, files, fingerprints, Minecraft versions and mod loaders
- Multi-source mods: CurseForge, Modrinth and GitHub Releases
- Profiles with their own game version, mod loader, release channel, output directory and mod list
- Ferium-style filters: game version (strict or minor), loader (prefer or any), release channel, and filename/title/description regex
- `add` / `remove` / `upgrade` workflow with recursive dependency resolution and parallel downloads
- Tracked modpacks from CurseForge (`manifest.json`) or Modrinth (`.mrpack`) with one-command upgrades
- `install` with recursive required-dependency resolution and a `--dry-run` preview
- `update` that identifies installed jars by CurseForge fingerprint and fetches newer compatible files
- Modpack installation from a local zip (`import`) or directly from CurseForge (`pack`)
- SHA1 checksum verification of downloads against API-provided hashes
- Automatic retry with backoff on `429` and common `5xx` responses
- Human-readable tables, plus `--json` output on list commands for scripting
- Cross-platform: Windows, Linux, macOS

## Requirements

- Python 3.8 or newer
- A CurseForge API key from [console.curseforge.com](https://console.curseforge.com/) (only needed for CurseForge commands)
- Modrinth needs no key. GitHub works unauthenticated; set `GITHUB_TOKEN` to raise the 60 requests/hour limit.

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

### Profiles

A profile stores the game version, mod loader, release channel, output directory and mod list, so the flags do not need repeating.

```bash
python cf.py profile create main --version 1.21.1 --loader fabric --channel release
python cf.py add jei sodium
python cf.py add owner/repo --source github
python cf.py add AANobbMI --source modrinth
python cf.py upgrade --dry-run
python cf.py upgrade
python cf.py list --verbose
```

Filters can be added on top of the simple flags. Each is `Type=Value` and repeatable:

```bash
python cf.py profile configure main --filter "Filename=.*fabric.*"
python cf.py profile configure main --filter "ModLoaderAny=fabric,quilt"
python cf.py profile configure main --filter "GameVersionMinor=1.21.1"
python cf.py profile configure main --clear-filters
```

Supported filter types: `GameVersionStrict`, `GameVersionMinor`, `ModLoaderPrefer`, `ModLoaderAny`, `ReleaseChannel`, `Filename`, `Title`, `Description`. Filters are applied to the candidate list from every source; GitHub candidates fall back to filename/release-text matching because GitHub has no Minecraft metadata.

If `profile create` is run without a name, version, or loader on an interactive terminal, it prompts for the missing values. In a script or CI it fails with guidance instead.

Profiles live in the same config file as the API key. The active profile is used by `add`, `remove`, `list`, `upgrade`, and as the default for `install`, `update` and `download ... latest`. Use `--profile <name>` on those commands or `cf profile use <name>` to switch. `cf profile configure <name> --version ... --loader ... --channel ...` changes settings later.

`add` and `install` detect the source: a numeric ID is CurseForge, `owner/repo` is GitHub, anything else is looked up on Modrinth first and then CurseForge. Force it with `--source`.

### Modpacks

```bash
python cf.py modpack add all-the-mods-10 --dir D:\packs\atm10
python cf.py modpack add fabulously-optimized --dir D:\packs\fo
python cf.py modpack list
python cf.py modpack upgrade
python cf.py modpack info
```

The source is detected automatically: numeric IDs are CurseForge, otherwise Modrinth is tried first and CurseForge is the fallback. CurseForge packs use `manifest.json`; Modrinth packs use `modrinth.index.json` with SHA1-verified files and `overrides/`.

Tracked modpacks record their source, project ID, installed file ID and target directory. `modpack upgrade` downloads the newer pack file and reinstalls its mods and overrides. `modpack delete` only stops tracking; files are left untouched.

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

`search` accepts `--version`, `--loader`, `--class-id`, `--category`, `--sort`, `--order`, `--limit`, `--page` and `--json`. Useful class ids for Minecraft: `6` mods, `17` worlds, `4471` modpacks. Search queries CurseForge only; Modrinth and GitHub are supported through `add` and `install`.

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

### Profiles and maintenance

| Command | Description |
|---|---|
| `profile create <name>` | Create a profile (`--version`, `--loader`, `--channel`, `--filter`, `--dir`, `--use`) |
| `profile configure <name>` | Change version, loader, channel, directory, or filters (`--clear-filters`) |
| `profile list` | List profiles |
| `profile use <name>` | Set the active profile |
| `profile show [name]` | Show a profile and its mods |
| `profile delete <name>` | Delete a profile (config only) |
| `add <mod>...` | Add mods (`--source auto\|curseforge\|modrinth\|github`, `--force` skips compatibility) |
| `remove <mod>...` | Remove mods by id, name, or slug |
| `list [--verbose] [--markdown]` | List mods in the active profile |
| `upgrade` | Download the latest compatible files for the profile |
| `modpack add <id\|slug>` | Install and track a CurseForge modpack |
| `modpack list / info / upgrade / delete / switch` | Manage tracked modpacks |

`install` and `update` target the active profile's directory, or `%APPDATA%\.minecraft\mods` when no profile exists; override with `--dir`. `install --dry-run` prints the full plan without writing files. `upgrade --dry-run` reports the plan, `--parallel N` sets simultaneous downloads, and `--clean` moves stale jars to `.old/` instead of deleting them. `update --dry-run` reports available updates only and never deletes old jars.

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

# Profile workflow: configure once, then add and upgrade
python cf.py profile create main --version 1.21.1 --loader fabric
python cf.py add jei sodium
python cf.py upgrade --dry-run
python cf.py upgrade
python cf.py list

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

- CurseForge requests go to `https://api.curseforge.com/v1` with the `x-api-key` header.
- Modrinth requests go to `https://api.modrinth.com/v2` and need no key. GitHub requests go to `https://api.github.com`, optionally with `GITHUB_TOKEN`.
- GitHub has no Minecraft metadata, so `upgrade` matches releases by checking the game version (with digit boundaries) and loader in the release text and asset filename. This is a heuristic, not a guarantee.
- The CurseForge API returns at most 50 results per page and caps pagination at `index + pageSize <= 10,000`.
- Fingerprints use CurseForge's modified Murmur2 hash (seed `1`, with tab, LF, CR and space bytes stripped), matching the official implementation.
- Downloads are verified against the SHA1 hash supplied by the API; a mismatch deletes the partial file and aborts.
- `429` and `5xx` responses are retried up to four attempts, honoring `Retry-After` when present.
- `upgrade` resolves required dependencies recursively and downloads up to 8 files in parallel (`--parallel`).
- Filters run over a unified candidate list (newest first) and intersect, with `ModLoaderPrefer` applied last, matching ferium's selection order.
- `.mrpack` paths are validated before extraction so a pack cannot write outside its target directory.
- Some projects disable third-party downloads; their files return no download URL and the command exits with a clear message.

## Testing

```bash
python -m unittest -v test_cf
```

The suite covers query construction, bulk request bodies, dependency resolution, multi-source resolution, filter selection, mrpack extraction, modpack tracking, retry behavior, checksum failure handling, GitHub version boundaries and the fingerprint implementation. The Modrinth and GitHub paths are also exercised against the live APIs during development.

## Project layout

```
cf.py        CLI implementation (stdlib only)
cf.cmd       Windows shim
test_cf.py   Unit tests
```

## Disclaimer

This is an unofficial client and is not affiliated with or endorsed by CurseForge or Overwolf. API usage is subject to the [CurseForge API terms](https://docs.curseforge.com/).
