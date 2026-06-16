# Perchance App Engine

A cross-platform (Windows / macOS / Linux) desktop launcher that turns any
public [perchance.org](https://perchance.org) generator into its own native app —
with isolated storage, unlimited cache that you fully control, and per-app
JavaScript injection.

***

## Quick Start

```bash
pip install PyQt6 PyQtWebEngine
python launcher.py
```

***

## Project Structure

```
perchance-app/
├── main.py                     ← Open this to launch
├── config.py                   ← Shared paths (edit if you move the folder)
├── global-overrides.js         ← JS injected into ALL generator apps
├── requirements.txt
│
├── launcher/
│   └── launcher.py             ← The app grid launcher UI
│
├── app_runner/
│   └── runner.py               ← Per-generator window (launched as subprocess)
│
├── gens/                       ← One folder per installed generator
│   └── <slug>/
│       ├── meta.json           ← Name, description, accent color
│       ├── overrides.js        ← JS injected only for this generator
│       └── favicon.png         ← App icon (optional)
│
└── data/                       ← All runtime data — back this up!
    └── <slug>/
        ├── cache/              ← HTTP disk cache (unlimited, never auto-deleted)
        ├── storage/            ← localStorage, IndexedDB, cookies
        └── files/              ← Downloaded/saved files
```

***

## Adding a Generator

### Via the Launcher UI
1. Click **＋ Add App**
2. Enter the generator's slug (the part after `perchance.org/`)
3. Optionally set a display name, description, and accent color
4. Click OK — the app appears in the grid

### Manually
```bash
mkdir gens/my-generator
echo '{"name":"My Generator","slug":"my-generator","color":"#8a4fff"}' > gens/my-generator/meta.json
echo 'console.log("hello");' > gens/my-generator/overrides.js
# optionally add gens/my-generator/favicon.png
```

***

## Running a Generator as a Standalone App

### From the Launcher
Click any card — it launches in its **own separate window** with its own process,
profile, cache, and storage. Multiple generators run simultaneously.

### Direct (bypassing the launcher entirely)
```bash
python app_runner/runner.py <slug>
# Example:
python app_runner/runner.py ai-character-generator
```

### Desktop Shortcuts
Right-click any app card → **🖥 Create Desktop Shortcut**.

| Platform | Result |
|---|---|
| Windows | `.lnk` shortcut (falls back to `.bat`) on Desktop |
| macOS | `.command` script on Desktop |
| Linux | `.desktop` entry on Desktop + `~/.local/share/applications/` |

***

## JavaScript Injection

Every time a generator page finishes loading, scripts run in this order:

```
Page load complete
    │
    ▼
global-overrides.js       ← runs for ALL generators
    │
    ▼
gens/<slug>/overrides.js  ← runs only for this generator
```

Click **⚡** in the app's chrome bar to re-run both scripts without reloading.

### Injecting CSS
```javascript
// In overrides.js — inject a <style> tag
const style = document.createElement('style');
style.textContent = `
  body { font-size: 16px !important; }
  .some-annoying-element { display: none !important; }
`;
document.head.appendChild(style);
```

***

## Storage & Cache

Everything lives under `./data/<slug>/` — **next to your script, never in a
system temp folder**.

| Path | Contents | Limit |
|---|---|---|
| `data/<slug>/cache/` | HTTP response cache | **Unlimited** |
| `data/<slug>/storage/` | localStorage, IndexedDB, cookies | **Unlimited** |
| `data/<slug>/files/` | Downloads and file saves | Disk only |

### Backup / Sync / Restore
Just copy or sync the entire `data/` folder. Each generator's data is fully
self-contained and portable.

```bash
# Backup
cp -r data/ ~/Dropbox/perchance-backup/

# Restore
cp -r ~/Dropbox/perchance-backup/ data/
```

***

## What This App Does NOT Have (By Design)

| Feature | Reason omitted |
|---|---|
| Login / account system | Handle perchance ads yourself in-browser |
| Direct API access | Respect perchance's terms — no data extraction |
| Edit mode / utility bar | Not an editor, just a runner |
| URL bar / free navigation | Each app is locked to its generator's URL |

***

## Customization

### Change accent color per app
Right-click app card → **✎ Edit App** → choose Accent color.

### Custom User-Agent
Edit `_setup_profile()` in `app_runner/runner.py` → `setHttpUserAgent(...)`.

### Allow popups (e.g. generators that open sub-windows)
In `runner.py`, change:
```python
s.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
```

### Chromium flags
```bash
# macOS / Linux
export QTWEBENGINE_CHROMIUM_FLAGS="--autoplay-policy=no-user-gesture-required"
python main.py

# Windows (set in System Properties → Environment Variables)
QTWEBENGINE_CHROMIUM_FLAGS=--autoplay-policy=no-user-gesture-required
```

***

## Dependencies

```
PyQt5>=5.15.0
PyQtWebEngine>=5.15.0
```

Both are available via pip and work on Windows, macOS, and Linux without
any additional system dependencies.

```bash
pip install PyQt5 PyQtWebEngine
```

On Linux you may also need:
```bash
sudo apt install python3-pyqt5.qtwebengine   # Debian/Ubuntu
# or
pip install PyQt5 PyQtWebEngine              # via pip (preferred)
```
