#!/usr/bin/env python3
"""Build latest.json from pCloud folder/file public links (and a few direct URLs).

Auth is optional. With PCLOUD_AUTH_TOKEN (or username/password via login?getauth=1),
the script can list every existing file publink, discover a folder by name, and
create a file publink only when a newly uploaded package has none.

Do not put secrets in this repo. Read them from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

API_HOST = (os.environ.get("PCLOUD_API_HOST") or "api.pcloud.com").strip() or "api.pcloud.com"
PUBLINK_PAGE = "https://u.pcloud.link/publink/show?code={code}"

ANDROID_SUFFIXES = (".apk",)
WINDOWS_SUFFIXES = (".exe", ".msi", ".msix", ".appx")

VERSION_IN_NAME = re.compile(
    r"(?i)(?:^|[^0-9])v(\d+(?:\.\d+){1,3})(?:[-_]((?:build)?\d+))?(?=$|[^0-9])"
)
# Fallback when the filename has digits but no leading 'v'
VERSION_IN_NAME_LOOSE = re.compile(
    r"(?i)(?:^|[^0-9])(\d+(?:\.\d+){2,3})(?:[-_]((?:build)?\d+))?(?=$|[^0-9])"
)
APK_VERSION = re.compile(r"^\d+(?:\.\d+){1,3}(?:[.-][A-Za-z0-9]+)?$")

ROOT = Path(__file__).resolve().parent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def pcloud_get(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://{API_HOST}/{method}"
    if query:
        url = f"{url}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"pCloud {method} HTTP {exc.code}") from exc
    if data.get("result", 0) != 0:
        raise RuntimeError(
            f"pCloud {method} failed: result={data.get('result')} error={data.get('error', data)}"
        )
    return data


def authenticate() -> str | None:
    """Return an auth token, or None if no credentials are configured."""
    token = os.environ.get("PCLOUD_AUTH_TOKEN", "").strip()
    if token:
        pcloud_get("userinfo", {"auth": token})
        return token

    username = os.environ.get("PCLOUD_USERNAME", "").strip()
    password = os.environ.get("PCLOUD_PASSWORD", "")
    if not username or not password:
        return None

    # Prefer login?getauth=1 — userinfo?getauth=1 can fail with result 1022.
    import hashlib

    device = "app-updates-" + hashlib.sha1(username.encode()).hexdigest()[:12]
    data = pcloud_get(
        "login",
        {
            "username": username,
            "password": password,
            "getauth": 1,
            "os": 4,
            "device": device,
        },
    )
    auth = data.get("auth")
    if not auth:
        raise RuntimeError("pCloud login succeeded but no auth token was returned.")
    return auth


def to_iso(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_version_from_filename(name: str) -> str | None:
    if not name:
        return None
    # Ignore trailing tokens that are not versions (Setup, Installer, signed, ...)
    m = VERSION_IN_NAME.search(name)
    if not m:
        m = VERSION_IN_NAME_LOOSE.search(name)
    if not m:
        return None
    version = m.group(1)
    suffix = m.group(2)
    if suffix:
        suffix = suffix.lstrip("-_")
        if suffix.lower().startswith("build"):
            version = f"{version}-{suffix}"
    return version


def parse_semver(version: str) -> tuple[int, ...]:
    core = (version or "").split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts) if parts else (0,)


def is_android(name: str) -> bool:
    return (name or "").lower().endswith(ANDROID_SUFFIXES)


def is_windows(name: str) -> bool:
    return (name or "").lower().endswith(WINDOWS_SUFFIXES)


def walk_files(meta: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(item: dict[str, Any]) -> None:
        if item.get("isfolder"):
            for child in item.get("contents") or []:
                walk(child)
            return
        name = item.get("name") or ""
        found.append(
            {
                "name": name,
                "fileid": item.get("fileid"),
                "size": int(item.get("size") or 0),
                "modified": item.get("modified") or item.get("created"),
                "code": None,
            }
        )

    walk(meta)
    return found


def show_publink(code: str) -> dict[str, Any]:
    return pcloud_get("showpublink", {"code": code})


def get_publink_download_url(code: str, fileid: int | None = None) -> str:
    params: dict[str, Any] = {"code": code}
    if fileid is not None:
        params["fileid"] = fileid
    data = pcloud_get("getpublinkdownload", params)
    hosts = data.get("hosts") or []
    path = data.get("path")
    if not hosts or not path:
        raise RuntimeError("getpublinkdownload returned no host/path")
    return "https://" + str(hosts[0]) + str(path)


def download_url(url: str, dest: Path, timeout: int = 180) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "80sRetroCar-app-updates/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)


def version_from_apk(path: Path) -> str | None:
    """Read versionName from the binary AndroidManifest string pool (no aapt needed)."""
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read("AndroidManifest.xml")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    strings = [
        s.decode("utf-16le")
        for s in re.findall(rb"(?:[\x20-\x7e]\x00){1,32}", data)
        if s
    ]
    candidates: list[str] = []
    after_name = False
    for s in strings:
        if s == "versionName":
            after_name = True
            continue
        if APK_VERSION.match(s) and "." in s:
            candidates.append(s)
            if after_name and s not in {"1.0", "0.0"}:
                # Prefer the first dotted token after the versionName label,
                # but keep scanning; some manifests list sdk strings first.
                pass
    dotted = [c for c in candidates if re.match(r"^\d+\.\d+", c)]
    if not dotted:
        return None
    # Prefer a 3-part version when both exist (e.g. 1.1.7 over a lone 1.0).
    dotted.sort(key=lambda v: (len(parse_semver(v)), parse_semver(v)), reverse=True)
    return dotted[0]


def head_url(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "80sRetroCar-app-updates/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return {
                "status": resp.status,
                "size": int(headers["content-length"]) if headers.get("content-length") else None,
                "modified": headers.get("last-modified"),
                "content_type": headers.get("content-type"),
                "final_url": resp.geturl(),
            }
    except urllib.error.HTTPError as exc:
        # Some hosts reject HEAD; fall back to a ranged GET.
        if exc.code in {403, 404, 405, 501}:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "80sRetroCar-app-updates/1.0",
                    "Range": "bytes=0-0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    size = None
                    cr = headers.get("content-range") or ""
                    m = re.search(r"/(\d+)$", cr)
                    if m:
                        size = int(m.group(1))
                    elif headers.get("content-length"):
                        size = int(headers["content-length"])
                    return {
                        "status": resp.status,
                        "size": size,
                        "modified": headers.get("last-modified"),
                        "content_type": headers.get("content-type"),
                        "final_url": resp.geturl(),
                    }
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(f"HEAD/GET {url} failed: {exc2}") from exc2
        raise RuntimeError(f"HEAD {url} failed: HTTP {exc.code}") from exc


class PCloud:
    def __init__(self, auth: str | None) -> None:
        self.auth = auth
        self.publinks: list[dict[str, Any]] = []
        self.by_code: dict[str, dict[str, Any]] = {}
        self.by_fileid: dict[Any, dict[str, Any]] = {}
        self.folder_links: list[dict[str, Any]] = []
        if auth:
            self._load_publinks()

    def _load_publinks(self) -> None:
        data = pcloud_get("listpublinks", {"auth": self.auth})
        self.publinks = data.get("publinks") or []
        for link in self.publinks:
            code = str(link.get("code") or "")
            if code:
                self.by_code[code] = link
            meta = link.get("metadata") or {}
            if meta.get("isfolder"):
                self.folder_links.append(link)
            fid = meta.get("fileid")
            if fid is not None:
                # First existing link for a file wins; we never replace it.
                self.by_fileid.setdefault(fid, link)
        log(f"Authenticated. Existing public links: {len(self.publinks)}")

    def discover_folder_code(self, names: list[str]) -> str | None:
        want = {n.strip().lower() for n in names if n and n.strip()}
        if not want:
            return None
        for link in self.folder_links:
            meta = link.get("metadata") or {}
            name = (meta.get("name") or "").strip().lower()
            if name in want:
                return str(link.get("code") or "") or None
        return None

    def code_for_fileid(self, fileid: Any, extra_codes: list[str]) -> str | None:
        link = self.by_fileid.get(fileid)
        if link and link.get("code"):
            return str(link["code"])
        for code in extra_codes:
            # already resolved below by the caller when possible
            cached = self.by_code.get(code)
            if cached and (cached.get("metadata") or {}).get("fileid") == fileid:
                return code
        return None

    def ensure_file_publink(self, fileid: Any) -> str | None:
        if not self.auth or fileid is None:
            return None
        existing = self.by_fileid.get(fileid)
        if existing and existing.get("code"):
            return str(existing["code"])
        data = pcloud_get("getfilepublink", {"auth": self.auth, "fileid": fileid})
        code = data.get("code")
        if not code:
            return None
        self.by_fileid[fileid] = data
        self.by_code[str(code)] = data
        log(f"Created file publink for fileid={fileid} (new upload had none)")
        return str(code)


def pick_newest(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not files:
        return None

    def key(item: dict[str, Any]) -> tuple:
        iso = to_iso(item.get("modified")) or ""
        ver = parse_version_from_filename(item.get("name") or "") or ""
        return (iso, parse_semver(ver), int(item.get("size") or 0))

    return max(files, key=key)


def resolve_file_codes(codes: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for code in codes:
        try:
            data = show_publink(code)
        except Exception as exc:  # noqa: BLE001
            log(f"  warning: file code {code} could not be read: {exc}")
            continue
        meta = data.get("metadata") or {}
        if meta.get("isfolder"):
            for f in walk_files(meta):
                if is_android(f["name"]) or is_windows(f["name"]):
                    # Folder accidentally listed as a "file code": no per-file code.
                    items.append(f)
            continue
        items.append(
            {
                "name": meta.get("name") or "",
                "fileid": meta.get("fileid"),
                "size": int(meta.get("size") or 0),
                "modified": meta.get("modified") or meta.get("created"),
                "code": code,
            }
        )
    return items


def merge_files(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[Any, dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []
    for group in groups:
        for item in group:
            fid = item.get("fileid")
            if fid is None:
                extras.append(item)
                continue
            prev = by_id.get(fid)
            if prev is None:
                by_id[fid] = dict(item)
            else:
                if not prev.get("code") and item.get("code"):
                    prev["code"] = item["code"]
                for key in ("name", "size", "modified"):
                    if not prev.get(key) and item.get(key):
                        prev[key] = item[key]
    return list(by_id.values()) + extras


def previous_platform(prev: dict[str, Any] | None, app_id: str, platform: str) -> dict[str, Any] | None:
    if not prev:
        return None
    app = (prev.get("apps") or {}).get(app_id)
    if not isinstance(app, dict):
        return None
    entry = app.get(platform)
    return entry if isinstance(entry, dict) else None


def reuse_or_extract_version(
    pkg: dict[str, Any],
    prev_entry: dict[str, Any] | None,
    folder_code: str | None,
) -> tuple[str, str]:
    """Return (version, version_source)."""
    name = pkg.get("name") or ""
    from_name = parse_version_from_filename(name)
    if from_name:
        return from_name, "filename"

    iso = to_iso(pkg.get("modified"))
    size = int(pkg.get("size") or 0)
    if prev_entry:
        same = (
            prev_entry.get("filename") == name
            and int(prev_entry.get("size") or 0) == size
            and prev_entry.get("updated") == iso
            and prev_entry.get("version")
            and prev_entry.get("version_source") != "modified"
        )
        if same:
            return str(prev_entry["version"]), str(prev_entry.get("version_source") or "previous")

    # Extract versionName from an APK when the filename has none.
    if is_android(name) and size and size <= 80 * 1024 * 1024:
        code = pkg.get("code") or folder_code
        if code:
            try:
                url = get_publink_download_url(code, pkg.get("fileid") if not pkg.get("code") else None)
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp) / "app.apk"
                    download_url(url, dest)
                    extracted = version_from_apk(dest)
                if extracted:
                    return extracted, "apk_manifest"
            except Exception as exc:  # noqa: BLE001
                log(f"  could not extract APK version from {name}: {exc}")

    if iso:
        # Date-based fallback, e.g. 2026.11.11 — noted so apps can treat it carefully.
        return iso[:10].replace("-", "."), "modified"
    return "0.0.0", "unknown"


def build_platform_entry(
    pkg: dict[str, Any],
    pcloud: PCloud,
    folder_code: str | None,
    extra_codes: list[str],
    prev_entry: dict[str, Any] | None,
    direct_url: str | None = None,
) -> dict[str, Any]:
    fileid = pkg.get("fileid")
    code = pkg.get("code") or pcloud.code_for_fileid(fileid, extra_codes)
    if not code and fileid is not None:
        code = pcloud.ensure_file_publink(fileid)

    if direct_url:
        url = direct_url
        url_source = "direct"
    elif code:
        url = PUBLINK_PAGE.format(code=code)
        url_source = "file_publink"
    elif folder_code:
        url = PUBLINK_PAGE.format(code=folder_code)
        url_source = "folder_publink"
    else:
        url = ""
        url_source = "none"

    version, version_source = reuse_or_extract_version(pkg, prev_entry, folder_code)
    entry: dict[str, Any] = {
        "version": version,
        "url": url,
        "updated": to_iso(pkg.get("modified")),
        "filename": pkg.get("name") or None,
        "size": int(pkg.get("size") or 0) or None,
        "version_source": version_source,
        "url_source": url_source,
    }
    return entry


def extract_direct_apk_version(url: str, dest_name: str, prev_entry: dict[str, Any] | None, size: int | None, iso: str | None) -> tuple[str, str]:
    from_name = parse_version_from_filename(dest_name)
    if from_name:
        return from_name, "filename"
    if prev_entry:
        same = (
            prev_entry.get("filename") == dest_name
            and int(prev_entry.get("size") or 0) == int(size or 0)
            and prev_entry.get("updated") == iso
            and prev_entry.get("version")
            and prev_entry.get("version_source") != "modified"
        )
        if same:
            return str(prev_entry["version"]), str(prev_entry.get("version_source") or "previous")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "app.apk"
            download_url(url, dest)
            extracted = version_from_apk(dest)
        if extracted:
            return extracted, "apk_manifest"
    except Exception as exc:  # noqa: BLE001
        log(f"  could not extract APK version from {url}: {exc}")
    if iso:
        return iso[:10].replace("-", "."), "modified"
    return "0.0.0", "unknown"


def build_direct_android(direct: dict[str, Any], prev_entry: dict[str, Any] | None) -> dict[str, Any]:
    url = direct.get("url")
    filename = direct.get("filename") or os.path.basename(urllib.parse.urlparse(url).path)
    info = head_url(url)
    iso = to_iso(info.get("modified"))
    size = info.get("size")
    version, version_source = extract_direct_apk_version(url, filename, prev_entry, size, iso)
    return {
        "version": version,
        "url": url,
        "updated": iso,
        "filename": filename,
        "size": size,
        "version_source": version_source,
        "url_source": "direct",
    }


def process_app(app: dict[str, Any], pcloud: PCloud, prev: dict[str, Any] | None) -> dict[str, Any]:
    app_id = app["id"]
    name = app["name"]
    folder_code = (app.get("folder_code") or "").strip() or None
    file_codes = [c for c in (app.get("file_codes") or []) if c]
    folder_names = list(app.get("folder_names") or [])
    if name and name not in folder_names:
        folder_names.append(name)

    if not folder_code and pcloud.auth:
        discovered = pcloud.discover_folder_code(folder_names)
        if discovered:
            folder_code = discovered
            log(f"{name}: discovered folder publink via API")

    packages: list[dict[str, Any]] = []
    if folder_code:
        try:
            shown = show_publink(folder_code)
            packages.extend(
                f
                for f in walk_files(shown.get("metadata") or {})
                if is_android(f["name"]) or is_windows(f["name"])
            )
        except Exception as exc:  # noqa: BLE001
            log(f"{name}: folder {folder_code} failed: {exc}")

    if file_codes:
        packages = merge_files(packages, resolve_file_codes(file_codes))

    # Attach existing file publink codes found via listpublinks.
    for pkg in packages:
        if not pkg.get("code") and pkg.get("fileid") is not None:
            found = pcloud.code_for_fileid(pkg["fileid"], file_codes)
            if found:
                pkg["code"] = found

    result: dict[str, Any] = {"name": name}

    direct = app.get("direct") or {}
    if direct.get("android"):
        result["android"] = build_direct_android(
            direct["android"], previous_platform(prev, app_id, "android")
        )
    else:
        android = pick_newest([p for p in packages if is_android(p["name"])])
        if android:
            result["android"] = build_platform_entry(
                android,
                pcloud,
                folder_code,
                file_codes,
                previous_platform(prev, app_id, "android"),
            )

    if direct.get("windows"):
        result["windows"] = build_direct_android(  # same shape; name is historical
            direct["windows"], previous_platform(prev, app_id, "windows")
        )
        # Re-label isn't needed; fields are identical.
    else:
        windows = pick_newest([p for p in packages if is_windows(p["name"])])
        if windows:
            result["windows"] = build_platform_entry(
                windows,
                pcloud,
                folder_code,
                file_codes,
                previous_platform(prev, app_id, "windows"),
            )

    if folder_code:
        result["folder_url"] = PUBLINK_PAGE.format(code=folder_code)

    return result


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def dump_latest(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate latest.json from pCloud public links.")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--output", default=str(ROOT / "latest.json"))
    args = parser.parse_args()

    config_path = Path(args.config)
    output_path = Path(args.output)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    prev = load_json(output_path)

    try:
        auth = authenticate()
    except Exception as exc:  # noqa: BLE001
        log(f"pCloud login failed ({exc}). Continuing with public links only.")
        auth = None
    if not auth:
        log("No pCloud credentials in the environment; using public folder/file links only.")

    pcloud = PCloud(auth)
    apps_out: dict[str, Any] = {}
    for app in config.get("apps") or []:
        log(f"Processing {app.get('name')} ({app.get('id')})...")
        entry = process_app(app, pcloud, prev)
        apps_out[app["id"]] = entry
        for alias in app.get("aliases") or []:
            alias_entry = json.loads(json.dumps(entry))
            # Keep a stable name per alias so a game can look itself up.
            pretty = alias.replace("-", " ").title()
            if alias == "zombie-smash":
                pretty = "Zombie Smash"
            elif alias == "neon-maze":
                pretty = "Neon Maze"
            alias_entry["name"] = pretty
            alias_entry["shared_with"] = app["id"]
            apps_out[alias] = alias_entry

    latest = {"generated": now_iso(), "apps": apps_out}
    text = dump_latest(latest)
    if output_path.exists() and output_path.read_text(encoding="utf-8") == text:
        log("latest.json unchanged.")
    else:
        output_path.write_text(text, encoding="utf-8")
        log(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
