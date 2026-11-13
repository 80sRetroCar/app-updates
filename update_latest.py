#!/usr/bin/env python3
"""Build latest.json from the website's latest.php manifest.

Files live in each app's Software/<App>/ folder on 80sretrocar.com.
download.php picks the newest; latest.php reports filename, version, size,
mtime, and a stable url. This script copies that into the public feed.

If latest.php is missing, is unexecuted PHP, or is not JSON (for example
the host does not run PHP yet), keep the previous latest.json and exit 0.
Do not crash, and do not wipe known versions.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = "https://80sretrocar.com/latest.php"
DEFAULT_DOWNLOAD = "https://80sretrocar.com/download.html?app={app}&kind={kind}"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def dump_latest(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def looks_like_php(text: str) -> bool:
    head = text.lstrip().lower()
    return head.startswith("<?php") or head.startswith("<?") or head.startswith("<script language=\"php\"")


def fetch_manifest(url: str) -> dict[str, Any] | None:
    """Return the parsed manifest, or None if it cannot be used."""
    headers = {
        "User-Agent": "80sRetroCar-app-updates/2.0",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        for attempt in range(2):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status = getattr(resp, "status", 200)
                    raw = resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                break
            except urllib.error.HTTPError as exc:
                # The host sometimes answers with a 409 "are you human" page that
                # just sets a cookie and reloads. Set that cookie and try once more.
                body = exc.read().decode("utf-8", "replace") if exc.code == 409 else ""
                m = re.search(r'document\.cookie\s*=\s*"([^"=;]+=[^";]*)', body)
                if attempt == 0 and m:
                    headers["Cookie"] = m.group(1)
                    continue
                raise
    except urllib.error.HTTPError as exc:
        log(f"manifest {url} HTTP {exc.code}: {exc.reason}. Keeping previous entries.")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"manifest {url} failed ({exc}). Keeping previous entries.")
        return None

    if status >= 400:
        log(f"manifest {url} HTTP {status}. Keeping previous entries.")
        return None

    text = raw.decode("utf-8", "replace")
    if looks_like_php(text):
        log("manifest looks like unexecuted PHP (the host may not be running PHP). Keeping previous entries.")
        return None
    if "text/html" in ctype and not text.lstrip().startswith("{"):
        log("manifest returned HTML, not JSON. Keeping previous entries.")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log(f"manifest is not JSON ({exc}). Keeping previous entries.")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        log("manifest JSON is missing an apps object. Keeping previous entries.")
        return None
    return data


def platform_entry(
    raw: dict[str, Any],
    app_id: str,
    kind: str,
    download_tmpl: str,
    prev: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    filename = raw.get("filename") or None
    if not version and filename:
        # Should already be filled by latest.php; leave a safe fallback.
        version = str(filename)
    if not version and prev and prev.get("version"):
        version = str(prev["version"])
    if not version:
        return None
    size = raw.get("size")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    updated = raw.get("mtime") or raw.get("updated") or None
    url = download_tmpl.format(app=app_id, kind=kind)
    return {
        "version": version,
        "url": url,
        "updated": updated,
        "filename": filename,
        "size": size or None,
        "version_source": raw.get("version_source") or "manifest",
        "url_source": "site_resolver",
    }


def previous_app(prev: dict[str, Any] | None, app_id: str) -> dict[str, Any] | None:
    if not prev:
        return None
    entry = (prev.get("apps") or {}).get(app_id)
    return entry if isinstance(entry, dict) else None


def has_platforms(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    return bool(entry.get("android") or entry.get("windows"))


def alias_entry(entry: dict[str, Any], alias: str, app_id: str) -> dict[str, Any]:
    out = json.loads(json.dumps(entry))
    pretty = alias.replace("-", " ").title()
    if alias == "zombie-smash":
        pretty = "Zombie Smash"
    elif alias == "neon-maze":
        pretty = "Neon Maze"
    out["name"] = pretty
    out["shared_with"] = app_id
    return out


def build_app(
    app: dict[str, Any],
    manifest_apps: dict[str, Any],
    download_tmpl: str,
    prev: dict[str, Any] | None,
) -> dict[str, Any] | None:
    app_id = app["id"]
    name = app.get("name") or app_id
    raw = manifest_apps.get(app_id)
    if not isinstance(raw, dict):
        # Aliases may appear under the canonical key only.
        raw = {}
    result: dict[str, Any] = {"name": raw.get("name") or name}
    prev_app = previous_app(prev, app_id)

    for kind in ("android", "windows"):
        incoming = raw.get(kind) if isinstance(raw.get(kind), dict) else None
        prev_plat = prev_app.get(kind) if prev_app and isinstance(prev_app.get(kind), dict) else None
        if incoming:
            entry = platform_entry(incoming, app_id, kind, download_tmpl, prev_plat)
            if entry:
                result[kind] = entry
                continue
        if prev_plat:
            # Manifest omitted this platform (no file on the site right now).
            # Keep the last known entry so a temporary empty folder does not
            # make in-app update checks forget the version.
            result[kind] = prev_plat

    if not has_platforms(result):
        if has_platforms(prev_app):
            log(f"{name}: no packages in the manifest; keeping the previous entry.")
            return json.loads(json.dumps(prev_app))
        return None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate latest.json from 80sretrocar.com/latest.php.")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--output", default=str(ROOT / "latest.json"))
    parser.add_argument("--manifest", default="")
    args = parser.parse_args()

    config_path = Path(args.config)
    output_path = Path(args.output)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    prev = load_json(output_path)

    manifest_url = (args.manifest or config.get("manifest_url") or DEFAULT_MANIFEST).strip()
    download_tmpl = (config.get("download_url") or DEFAULT_DOWNLOAD).strip()

    log(f"Fetching {manifest_url} ...")
    manifest = fetch_manifest(manifest_url)
    if manifest is None:
        if prev and has_platforms_any(prev):
            log("left latest.json unchanged.")
            return 0
        log("no previous latest.json to keep.")
        return 0

    manifest_apps = manifest.get("apps") or {}
    apps_out: dict[str, Any] = {}
    for app in config.get("apps") or []:
        app_id = app["id"]
        log(f"Processing {app.get('name')} ({app_id})...")
        entry = build_app(app, manifest_apps, download_tmpl, prev)
        if not entry:
            log(f"{app.get('name')}: no packages yet.")
            continue
        apps_out[app_id] = entry
        for alias in app.get("aliases") or []:
            apps_out[alias] = alias_entry(entry, alias, app_id)

    if not apps_out:
        if prev and (prev.get("apps") or {}):
            log("manifest had no usable apps; keeping the previous latest.json.")
            return 0
        log("nothing to write.")
        return 0

    prev_apps = (prev or {}).get("apps")
    if prev_apps == apps_out and output_path.exists():
        log("latest.json unchanged.")
        return 0

    latest = {"generated": now_iso(), "apps": apps_out}
    output_path.write_text(dump_latest(latest), encoding="utf-8")
    log(f"Wrote {output_path}")
    return 0


def has_platforms_any(prev: dict[str, Any]) -> bool:
    for entry in (prev.get("apps") or {}).values():
        if has_platforms(entry if isinstance(entry, dict) else None):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
