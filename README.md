# App updates

Public “what’s the latest version?” file for apps from [www.80sRetroCar.com](https://www.80sRetroCar.com).

Each app can check **one URL** when it opens. If a newer version is listed, show **Update available** and open the download link.

## Public URL

https://raw.githubusercontent.com/80sRetroCar/app-updates/main/latest.json

How to hook an app up: see [INTEGRATION.md](INTEGRATION.md).

## After you upload a new file

1. Upload the new APK or EXE to that app’s folder on the website, for example `Software/Retro-Drop/RetroDrop-v1.3.0.apk`.
2. Put the version in the filename, like `RetroNotes-v2.2.3.apk` or `RetroNotes-Windows-v2.4.2.exe`.
3. This repo reads `https://80sretrocar.com/latest.php` every 30 minutes (or run **Actions → Update latest.json → Run workflow**) and updates `latest.json` only when something changed.

You do **not** have to edit this repo, and you do **not** have to change the website, for a normal release. The Download button and the in-app update link both resolve the newest file in that folder.

Each platform entry’s `url` is `https://80sretrocar.com/download.html?app=<key>&kind=android` (or `windows`). That page redirects to `download.php`, which starts the download of the newest matching file.

If `latest.php` cannot be read (404, the host is not running PHP yet, or the body is not JSON), this script keeps the last known `latest.json` and does not fail the job.

## Folder mapping

| Key | Folder(s) on the site |
| --- | --- |
| `valiant-arcade` | `Software/Valiant-Arcade` |
| `retro-notes` | `Software/Retro-Notes` |
| `retrodrop` | `Software/Retro-Drop` |
| `retrolevel` | `Software/RetroLevel` |
| `retro-grid` | `Software/Retro-Grid` |
| `retroride` | `Software/RetroRide` |
| `80s-retro-car-games` (aliases `zombie-smash`, `neon-maze`) | `Software/Neon-Maze` **and** `Software/Zombie-Smash` |
| `retro-locker` | `Software/RetroLocker` |
| `retrolauncher` | `Software/RetroLauncher` |

Neon Maze and Zombie Smash share one APK, named like `80sRetroCarGames-v1.23.apk`. Upload it to either folder (Neon-Maze is the recommended home). Both download links look in both folders and pick the newest, so you only upload it once.

Coming Soon apps that are **not finished** (RetroSaver; Good Lord, What Did You Eat?; The Adventures of Skyroogle) are excluded from `latest.php` and from this feed, even if a file is already in their folder. They stay on the Coming Soon page with no download button.

RetroLauncher and Retro Locker are still Coming Soon on the website (no download buttons) but their folders are scanned, so a file there can appear in the feed once uploaded.

## Adding an app

Edit `config.json` and add a matching key to the whitelist in `releases.php` on the website (folder paths are hardcoded there for safety).
