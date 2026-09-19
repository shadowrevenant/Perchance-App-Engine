# Jawless Perchance App Engine

Jawless Perchance App Engine is a Windows, macOS, and Linux desktop launcher
for public `perchance.org` generators. Each generator has its own persistent
browser profile, cache, downloads, and optional JavaScript overrides. The
bundle also includes a searchable image gallery, optional local ONNX tagging
and upscaling, and an offline TiddlyWiki notes window.

This is an independent, unofficial project. It is not affiliated with or
endorsed by Perchance, TiddlyWiki, Hugging Face, or the model authors.

## Features

- Isolated cache, cookies, storage, and downloads for each generator.
- Global and per-generator JavaScript overrides.
- SQLite image gallery with tags, ratings, prompt metadata, and thumbnails.
- Optional local WD SwinV2 tagging and ONNX upscaling.
- Offline TiddlyWiki notes, using the same PyQt6 runtime as the main app.
- Configurable downloads, window behavior, inference provider, and models.

## Requirements

- Python 3.10 or newer; Python 3.12 is recommended.
- Approximately 1 GB for Qt and a local virtual environment.
- An internet connection for Perchance pages and optional model download.
- The AI tools require additional packages and about 468 MB for the tagger.

Do not install the dependencies globally. Use the project-local virtual
environment shown below.

## Windows setup

Working directory: the extracted `Jawless` folder.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app-engine.py
```

If you downloaded the complete Windows portable release, do not run these setup
steps. Extract the entire archive and double-click `Start-Jawless.cmd`; its
embedded runtime and tagger model are already included.

To enable local AI tools with CPU inference:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
.\.venv\Scripts\python.exe imagetools.py --download
.\.venv\Scripts\python.exe imagetools.py --verify
```

For NVIDIA inference, install a CUDA-compatible `onnxruntime-gpu` build in
place of `onnxruntime`. Check ONNX Runtime's CUDA/cuDNN compatibility before
changing the package.

## Linux setup

Some distributions require their `python3-venv` package before the launcher
can run (for example, `python3-venv` on Debian or Ubuntu). PyQt and Qt
WebEngine are installed from `requirements.txt`; the operating system may
also need the usual desktop libraries required by Qt.

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python app-engine.py
```

Optional CPU AI tools:

```bash
./.venv/bin/python -m pip install -r requirements-ai.txt
./.venv/bin/python imagetools.py --download
./.venv/bin/python imagetools.py --verify
```

## macOS setup

Use a supported Python 3 distribution and a local virtual environment.

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python app-engine.py
```

Optional CPU AI tools use the same commands as Linux. ONNX Runtime may expose
CoreML depending on the installed build and machine.

## Running components directly

```text
python launcher.py
python app_runner/runner.py <generator-slug> --root <program-root>
python gallery.py
python twnotes.py
python configurate.py
```

Run `app-engine.py` for normal use so the first-run GPL notice is displayed.

## Adding a generator

Use **Add App** in the launcher and enter the portion after
`https://perchance.org/`. The launcher creates:

```text
gens/<slug>/meta.json
gens/<slug>/overrides.js
```

Runtime data is stored under:

```text
data/<slug>/cache/
data/<slug>/storage/
data/<slug>/files/
data/_gallery/gallery.db
data/_gallery/thumbs/
```

Back up the entire `data` directory to preserve cookies, browser storage,
downloads, gallery tags, ratings, and prompt metadata.

## Browser compatibility mode

The default configuration preserves normal Chromium certificate validation and
site isolation. If Perchance or Cloudflare will not load in the embedded
browser, open **Configure → Advanced** and enable **Legacy Perchance
compatibility mode**, then close and reopen all generator windows.

That mode uses certificate-error, insecure-content, and reduced site-isolation
flags. It should remain off unless it is required for a known compatibility
problem. Do not use it for unrelated browsing or while handling sensitive
accounts. The runner intentionally has no general URL bar.

## JavaScript overrides

Scripts are injected in this order at document-ready time:

1. `global-overrides.js` or `global-override.js`, if present.
2. `gens/<slug>/overrides.js`, if present.

The per-generator script runs last. Use the lightning button to re-run the
scripts on the current document.

## Local model integrity

The built-in downloader pins the official files currently used by this build:

| File | Bytes | SHA-256 |
|---|---:|---|
| `model.onnx` | 467,460,978 | `e6774bff34d43bd49f75a47db4ef217dce701c9847b546523eb85ff6dbba1db1` |
| `selected_tags.csv` | 308,468 | `298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217` |

Run `python imagetools.py --verify` after copying or restoring model files.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q .
```

The source tree should be placed under version control before active feature
development or distribution. Runtime data, environments, caches, models, and
license-acceptance state are excluded by `.gitignore`.

## License

Jawless Perchance App Engine is free software distributed under the GNU General
Public License version 3. The complete terms are in `LICENSE`. Recipients may
use, study, modify, and redistribute the program under GPLv3, including for
commercial purposes, and distributed derivatives must remain GPL-compatible.

Bundled libraries, models, and content retain their respective licenses. See
`THIRD_PARTY_NOTICES.md` in the portable distribution.
