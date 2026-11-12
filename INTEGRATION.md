# Checking for updates

Have each app request this URL, at most once per day:

```
https://raw.githubusercontent.com/80sRetroCar/app-updates/main/latest.json
```

No login is required. If the request fails or the user is offline, skip the prompt.

## App keys

Use the key that matches the app:

| Key | App | Platforms in the file now |
| --- | --- | --- |
| `valiant-arcade` | Valiant Arcade | Android |
| `retro-notes` | Retro Notes | Android, Windows |
| `retrodrop` | RetroDrop | Android, Windows |
| `retrolevel` | RetroLevel | Android |
| `retro-grid` | Retro Grid | Android, Windows |
| `retroride` | RetroRide | Android |
| `80s-retro-car-games` | 80s Retro Car Games (Zombie Smash + Neon Maze, one APK) | Android |
| `zombie-smash` | Zombie Smash (same APK as above) | Android |
| `neon-maze` | Neon Maze (same APK as above) | Android |

Example shape:

```json
{
  "generated": "2026-11-11T22:45:00Z",
  "apps": {
    "retro-notes": {
      "name": "Retro Notes",
      "android": {
        "version": "2.2.2",
        "url": "https://80sretrocar.com/download.html?app=retro-notes&kind=android",
        "updated": "2026-11-11T21:04:18Z"
      },
      "windows": {
        "version": "2.4.1",
        "url": "https://80sretrocar.com/download.html?app=retro-notes&kind=windows",
        "updated": "2026-11-11T22:03:01Z"
      }
    }
  }
}
```

There may be extra fields (`filename`, `size`, `version_source`). You only need `version` and `url`.

## Rules

1. Check on launch. Store the last-check time and do not check again until 24 hours have passed. A hidden “Check for updates” button can ignore that limit.
2. Read `apps["your-key"].android` on Android, or `.windows` on Windows. If that object is missing, do nothing.
3. Compare the remote `version` to the app’s own version with a **semantic** (numeric) compare: `2.6.17` is newer than `2.6.9`, and `2.3` is treated as `2.3.0`. Ignore a leading `v`. A suffix like `-build4` is only a tie-breaker after the numeric part.
4. If the remote version is newer **and** `url` starts with `https://`, show a dialog titled **Update available**, with a short message and a **Download** button that opens `url` (the system browser). That URL is a small page on 80sretrocar.com which starts the download of the newest file in the app’s folder. Include a **Later** / dismiss button. Never auto-download or auto-install.
5. If `version_source` is `"modified"`, the file name had no version and the value is a date like `2026.11.11`. You can still compare it, but it is better to name uploads `App-v1.2.3.apk`.

Apps that already read a Firebase `latest.json` (`versionName`, `whats_new`) need a one-time change: point at this URL and read `apps[key].android.version` instead of `android.versionName`.

## Android (Kotlin)

```kotlin
const val LATEST_URL =
    "https://raw.githubusercontent.com/80sRetroCar/app-updates/main/latest.json"
const val APP_KEY = "retro-notes" // change per app
const val PREFS = "app_updates"
const val LAST_CHECK = "last_check_ms"
const val DAY_MS = 24L * 60 * 60 * 1000

fun shouldCheck(prefs: android.content.SharedPreferences): Boolean {
    val last = prefs.getLong(LAST_CHECK, 0L)
    return System.currentTimeMillis() - last >= DAY_MS
}

/** true if remote is a newer semantic version than local. */
fun isNewer(remote: String, local: String): Boolean {
    fun parts(v: String): List<Int> {
        val core = v.trim().removePrefix("v").removePrefix("V")
            .split("-", limit = 2)[0]
        return core.split(".").mapNotNull { it.toIntOrNull() }
    }
    val a = parts(remote)
    val b = parts(local)
    val n = maxOf(a.size, b.size)
    for (i in 0 until n) {
        val av = a.getOrElse(i) { 0 }
        val bv = b.getOrElse(i) { 0 }
        if (av != bv) return av > bv
    }
    return false
}

fun checkForUpdate(activity: android.app.Activity) {
    val prefs = activity.getSharedPreferences(PREFS, android.content.Context.MODE_PRIVATE)
    if (!shouldCheck(prefs)) return
    Thread {
        try {
            val json = java.net.URL(LATEST_URL).readText()
            val root = org.json.JSONObject(json)
            val app = root.getJSONObject("apps").optJSONObject(APP_KEY) ?: return@Thread
            val android = app.optJSONObject("android") ?: return@Thread
            val version = android.optString("version")
            val url = android.optString("url")
            val local = activity.packageManager
                .getPackageInfo(activity.packageName, 0).versionName ?: return@Thread
            prefs.edit().putLong(LAST_CHECK, System.currentTimeMillis()).apply()
            if (!url.startsWith("https://") || !isNewer(version, local)) return@Thread
            activity.runOnUiThread {
                android.app.AlertDialog.Builder(activity)
                    .setTitle("Update available")
                    .setMessage("Version $version is available. You have $local.")
                    .setPositiveButton("Download") { _, _ ->
                        activity.startActivity(
                            android.content.Intent(
                                android.content.Intent.ACTION_VIEW,
                                android.net.Uri.parse(url)
                            )
                        )
                    }
                    .setNegativeButton("Later", null)
                    .show()
            }
        } catch (_: Exception) {
            // Offline or bad JSON: skip quietly.
        }
    }.start()
}
```

Call `checkForUpdate(this)` once from `onResume` (or `onCreate`) of your main activity.

## Windows (C# / .NET)

```csharp
const string LatestUrl =
    "https://raw.githubusercontent.com/80sRetroCar/app-updates/main/latest.json";
const string AppKey = "retro-notes"; // change per app
const string PrefsName = "app-updates-last-check";

static bool IsNewer(string remote, string local)
{
    static int[] Parts(string v)
    {
        var core = v.Trim().TrimStart('v', 'V').Split('-')[0];
        return core.Split('.').Select(p => int.TryParse(p, out var n) ? n : 0).ToArray();
    }
    var a = Parts(remote);
    var b = Parts(local);
    var n = Math.Max(a.Length, b.Length);
    for (var i = 0; i < n; i++)
    {
        var av = i < a.Length ? a[i] : 0;
        var bv = i < b.Length ? b[i] : 0;
        if (av != bv) return av > bv;
    }
    return false;
}

static async Task CheckForUpdateAsync(string localVersion)
{
    var lastPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "80sRetroCar", PrefsName);
    if (File.Exists(lastPath) &&
        DateTime.TryParse(File.ReadAllText(lastPath), out var last) &&
        DateTime.UtcNow - last.ToUniversalTime() < TimeSpan.FromDays(1))
        return;

    using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
    using var doc = System.Text.Json.JsonDocument.Parse(await http.GetStringAsync(LatestUrl));
    File.WriteAllText(lastPath, DateTime.UtcNow.ToString("o"));
    if (!doc.RootElement.GetProperty("apps").TryGetProperty(AppKey, out var app)) return;
    if (!app.TryGetProperty("windows", out var win)) return;
    var version = win.GetProperty("version").GetString() ?? "";
    var url = win.GetProperty("url").GetString() ?? "";
    if (!url.StartsWith("https://", StringComparison.Ordinal) || !IsNewer(version, localVersion))
        return;

    var result = MessageBox.Show(
        $"Version {version} is available. You have {localVersion}.",
        "Update available",
        MessageBoxButtons.YesNo,
        MessageBoxIcon.Information);
    if (result == DialogResult.Yes)
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
}
```

Call this on startup (once). Use `windows` on the desktop app and `android` in a WinUI/MAUI Android build. For a WinForms or WPF dialog, Yes is **Download** and No is **Later**.

## Other stacks

Any language works the same way:

1. `GET` the URL at most once per day (plain HTTPS, no auth).
2. Parse JSON and read `apps[your-key].android` or `.windows`.
3. Compare versions numerically, not as plain strings (`"2.6.17" > "2.6.9"`).
4. If remote is newer, show **Update available** and open `url` in the system browser.

Java apps in this family can keep their existing `Updater` class and change only the URL plus the JSON path (`apps.getJSONObject(APP_KEY).getJSONObject("android").getString("version")` instead of `android.versionName`). Go, Flutter, and HTML wrappers follow the same four steps.
