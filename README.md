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

Not covered: games that save to the registry. Games that save in their install folder keep their saves there.

## Importing saves from another PC

Games read saves from the shared saves folder, not from your real `~/Documents`. Copy saves from a Windows PC with **Import a game's save folder…** (or **Import a whole Documents folder…**) in the Saves section, or:

```bash
python3 -m steamkit saves import ~/Documents/NFSTR --dry-run   # preview
python3 -m steamkit saves import ~/Documents/NFSTR
python3 -m steamkit saves import /path/to/WindowsDocuments --contents
```

Pick the folder that sits directly under Documents (for example `Criterion Games`, not the game folder inside it), so the path stays the same as on Windows. Config, ini and graphics files are left out so a PC's resolution doesn't land on a handheld (`--include-settings` brings them in). Newer files replace older ones after a backup. Nothing is deleted. A game's folder only gets linked after its first launch, so a new game is linked within about 30 seconds of closing it; import or sync its saves before the second launch.

## LAN sync

Needs shared saves on. Off by default. Syncs save folders between your devices (for example a Steam Deck and a GPD Win 4) over the local network.

1. Turn on **LAN sync** on both devices.
2. On one, choose **Show pairing code**. On the other, choose **Find devices**, pick the first one and enter the code. This is done once.
3. Choose **Sync with …**. Folders that look like game saves are listed first. Tick the ones to sync. Folders that only exist on the other device are listed too, so a new device can start from theirs. The selection is remembered.
4. Pick a direction: newest file wins both ways, get from the other device, or send to it.

Rules:

- Files are never deleted by a sync.
- Settings files (config, ini, cfg, graphics, video, options, logs) and cache/screenshot folders are not synced by default, so each device keeps its own resolution. Turn this off on the sync page if you want them.
- If a file differs on both sides, the newer one wins. Same time but different content is left alone and reported.
- A backup of `~/SteamKitSaves` is taken before any file is overwritten, on the receiving device too.
- Sync is refused while a game is running.
- Symlinks are not followed. Only folders inside the shared saves folder can be synced.

Security: devices must be paired. Requests are signed with a key made at pairing, but file contents are not encrypted. Use it on a network you trust. Ports: UDP 47621 (discovery) and TCP 47622. If a firewall is on, allow them. If **Find devices** shows nothing (some routers block broadcast), use the CLI with `--host <ip>`.

From the CLI:

```bash
python3 -m steamkit lan serve --pair      # make this device visible, print a pairing code
python3 -m steamkit lan pair              # on the other device: enter the code
python3 -m steamkit lan track             # choose folders
python3 -m steamkit lan sync --mode newest
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
