# Third-party notices

Jawless Perchance App Engine is distributed under GPLv3. The portable package
also contains independent third-party components governed by their own terms.
Those licenses are not replaced or restricted by the application's GPL.

## Python 3.12.10

- Project: Python
- License: Python Software Foundation License
- Source: <https://www.python.org/downloads/release/python-31210/>

## PyQt6 6.11.0 and PyQt6-WebEngine 6.11.0

- Project: Riverbank Computing PyQt
- License: GNU General Public License version 3
- Source: <https://www.riverbankcomputing.com/software/pyqt/>

The PyQt wheels include Qt 6.11.2 libraries. Qt components are provided under
their applicable LGPL/GPL terms, and Qt WebEngine includes Chromium and other
third-party components. The portable bundle preserves the license files shipped
inside the installed Python distributions.

- Qt licensing: <https://doc.qt.io/qt-6/licensing.html>
- Qt WebEngine licensing: <https://doc.qt.io/qt-6/qtwebengine-licensing.html>
- Qt third-party licenses: <https://doc.qt.io/qt-6/licenses-used-in-qt.html>

## ONNX Runtime 1.30.0

- Project: Microsoft ONNX Runtime
- License: MIT
- Source: <https://github.com/microsoft/onnxruntime>

## NumPy 2.5.3

- Project: NumPy
- License: BSD-3-Clause
- Source: <https://github.com/numpy/numpy>

## WD SwinV2 Tagger v3

- Project: `SmilingWolf/wd-swinv2-tagger-v3`
- Revision: `627aef95638667ddcaa3ac8ae625e88ea5b02f51`
- License declared by the model repository: Apache License 2.0
- Source: <https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3>

The model card states that the model was trained on Danbooru images. Model and
dataset licensing are separate from the Jawless application license. Users are
responsible for evaluating whether model use and generated classifications are
appropriate for their jurisdiction and intended use.

## TiddlyWiki 5.4.1

- Project: TiddlyWiki
- License: BSD-3-Clause
- Source: <https://github.com/TiddlyWiki/TiddlyWiki5>

The bundled `datastore.html` contains TiddlyWiki's own copyright and license
notices.

## Complete license material

The portable distribution includes a `licenses/` directory containing license
files copied from the installed wheels and upstream projects. Recipients may
replace or inspect the separately stored Python packages and Qt libraries.
