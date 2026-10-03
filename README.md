# SteamKit

Decky plugin for SteamOS and CachyOS Gaming Mode. Add Windows games to Steam in bulk, with Proton and artwork, using a controller.

## Features

- Mount external drives at boot. No password prompt after reboot.
- Scan a folder, pick the exes you want, add them to Steam in one go.
- Set the compatibility tool for all of them (Proton, Proton Experimental, GE-Proton).
- Artwork and icons from SteamGridDB. Searches by folder name, then exe name. Manual search if nothing matches.
- Remove games added by SteamKit. Game files are not touched.
- Shared saves (optional): every game saves to one folder, in Gaming Mode and on desktop. Backup and restore.

## Install

Requires [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader).

1. Download `SteamKit.zip` from [Releases](../../releases).
2. Decky settings → Developer → Install plugin from ZIP.

Or from a terminal:

```bash
sudo unzip -o SteamKit.zip -d ~/homebrew/plugins/ && sudo systemctl restart plugin_loader
```

## Setup

Add a SteamGridDB API key under Settings in the plugin. Get one at steamgriddb.com → Preferences → API.

## Shared saves

Off by default. When on, these folders in each game's Proton prefix (and `~/.wine` if enabled) link to `~/SteamKitSaves/Saves`:

- Documents
- Saved Games
- AppData/Roaming, AppData/Local, AppData/LocalLow
- Public Documents

Existing saves are moved in, never overwritten. If two games have the same file, both are kept.
Backups go to `~/SteamKitSaves/Backups`. Restoring backs up the current saves first.

Not covered: games that save to the registry. Games that save in their install folder keep their saves there.

## Notes

- In Gaming Mode, games are added through Steam's client API. Restart Steam to show icons.
- Drive setup adds an entry to `/etc/fstab`. A backup is kept at `/etc/fstab.steamkit.bak`.
- exFAT and NTFS work, but ext4 or btrfs is more reliable with Proton.

## CLI

The core also runs without Decky:

```bash
python3 -m steamkit mount
python3 -m steamkit add /path/to/games
python3 -m steamkit list
python3 -m steamkit remove
```

Steam must be closed for `add` and `remove`. The CLI and the plugin keep separate lists of added games.

## Build

```bash
decky/scripts/package.sh
```

Builds in Docker and writes `decky/out/SteamKit.zip`. No Node needed on the host.

Tests:

```bash
python3 -m unittest discover -s tests
```

## License

MIT
