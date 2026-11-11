# App updates

Public “what’s the latest version?” file for apps from [www.80sRetroCar.com](https://www.80sRetroCar.com).

Each app can check **one URL** when it opens. If a newer version is listed, show **Update available** and open the download link.

## Public URL

https://raw.githubusercontent.com/80sRetroCar/app-updates/main/latest.json

How to hook an app up: see [INTEGRATION.md](INTEGRATION.md).

## After you upload a new file

1. Upload the new APK or EXE to that app’s existing pCloud folder.
2. Put the version in the filename, like `RetroNotes-v2.2.3.apk` or `RetroNotes-Windows-v2.4.2.exe`.
3. This repo checks pCloud every 30 minutes (or run **Actions → Update latest.json → Run workflow**) and updates `latest.json` only when something changed.

You do **not** have to edit this repo by hand for a normal release.

## Adding an app

Edit `config.json`:

- `folder_code` — the pCloud **folder** share code (best). The script then always picks the newest APK and newest Windows installer in that folder.
- `file_codes` — existing pCloud **file** share codes, used as stable download URLs when they still point at the current file.
- `folder_names` — optional names used to find a folder if you add pCloud credentials (see below).
- `direct` — a regular https URL (used for the games APK hosted on 80sretrocar.com).

## Optional GitHub secrets

`latest.json` and `config.json` only contain public share links. No passwords belong in this repo.

If you add these Actions secrets (the same values already used by the private `download-report` repo), the job can:

- find a folder by name when `folder_code` is missing
- reuse an existing file share instead of making a new one
- create a file share **only** when a newly uploaded APK/EXE has no public link yet

| Secret | Required? |
| --- | --- |
| `PCLOUD_AUTH_TOKEN` | Preferred. Token from `login?getauth=1`. |
| `PCLOUD_USERNAME` / `PCLOUD_PASSWORD` | Fallback if there is no token. |
| `PCLOUD_API_HOST` | Optional. Defaults to `api.pcloud.com`. |

Without those secrets, the script still works for every app that already has a folder or file share in `config.json`. New uploads in a folder will be detected; the download URL may be the folder page until a file share exists.
