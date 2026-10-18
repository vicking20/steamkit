# SteamKit

Decky plugin for SteamOS and CachyOS Gaming Mode. Add Windows games to Steam in bulk, with Proton and artwork, using a controller.

## Features

- Mount external drives at boot. No password prompt after reboot.
- Scan a folder, pick the exes you want, add them to Steam in one go.
- Set the compatibility tool for all of them (Proton, Proton Experimental, GE-Proton).
- Artwork and icons from SteamGridDB. Searches by folder name, then exe name. Manual search if nothing matches.
- Remove games added by SteamKit. Game files are not touched.
- Shared saves (optional): every game saves to one folder, in Gaming Mode and on desktop. Backup and restore.
- LAN sync (optional): find other SteamKit devices on your network and sync the save folders you pick.

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

A game's folder is linked after its first launch (within about 30 seconds of closing it).

Not covered: games that save to the registry, or in their install folder.

### Import saves from another PC

Use **Import a game's save folder…** or **Import a whole Documents folder…** in the Saves section. Pick the folder directly under Documents (for example `Criterion Games`). Newer files replace older ones after a backup. Nothing is deleted. Config, ini and graphics files are left out so a PC's resolution doesn't carry over.

```bash
python3 -m steamkit saves import ~/Documents/NFSTR --dry-run
python3 -m steamkit saves import /path/to/WindowsDocuments --contents
```

## LAN sync

Sync save folders between your own devices on the same network. Needs shared saves on.

1. Turn on **LAN sync** on both devices.
2. On one, choose **Show pairing code**. On the other, choose **Find devices**, pick it and enter the code (once).
3. Choose **Sync with …**, tick the folders to sync, and pick a direction: newest wins, get, or send. Your selection is remembered.

Files are never deleted. Settings files (config, ini, graphics, logs) are not synced by default, so each device keeps its own resolution. A backup is taken before anything is overwritten, and sync is refused while a game is running.

Devices must be paired and requests are signed, but file contents are not encrypted. Use it on a network you trust. It uses UDP 47621 and TCP 47622. If a router blocks discovery, use the CLI with `--host <ip>`:

```bash
python3 -m steamkit lan serve --pair
python3 -m steamkit lan pair
python3 -m steamkit lan track
python3 -m steamkit lan sync
```

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
