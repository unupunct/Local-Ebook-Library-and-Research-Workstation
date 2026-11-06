# Atlas Library on Linux (ParrotOS, Debian, Ubuntu, Kali …)

Atlas Library runs unchanged on Linux: the same Python/PySide6 code, the same database format, the same features.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/unupunct/Local-Ebook-Library-and-Research-Workstation/main/install.sh | bash
```

Read the script first if you like (`curl -fsSL <url> | less`): it is ~150 lines. It

1. installs the system libraries Qt needs with `apt` (asks for `sudo` only for this step; skipped if everything is present or with `--no-deps`),
2. downloads the source into `~/.local/opt/atlas-library/app`,
3. creates a virtual environment in `~/.local/opt/atlas-library/venv` and installs the Python packages from PyPI,
4. adds the command `~/.local/bin/atlas-library` and an entry in the application menu (Office),
5. runs the built-in self-test (indexes and searches a synthetic library in every supported format).

Nothing is installed system-wide except the apt libraries. Re-running the command **updates** the installation; your library is never touched.

Options (after `bash -s --`): `--ref v1.2.0` (a tag or branch instead of `main`), `--with-ocr` (also installs Tesseract), `--no-deps`, `--uninstall`, `--purge` (with `--uninstall`: also delete the library data).

```bash
curl -fsSL https://raw.githubusercontent.com/unupunct/Local-Ebook-Library-and-Research-Workstation/main/install.sh | bash -s -- --with-ocr
curl -fsSL https://raw.githubusercontent.com/unupunct/Local-Ebook-Library-and-Research-Workstation/main/install.sh | bash -s -- --uninstall
```

## Supported systems

What the automated checks (GitHub Actions, every push) actually cover:

| System | Python | Checked |
|---|---|---|
| **ParrotOS 7** (official `parrotsec/core` image, Debian 13 based), x86_64 | 3.13 | the one-line command, the full test suite on a real X11 display (Xvfb) and with Qt's offscreen platform, the GUI starting and staying up, menu entry validation, uninstall, and screenshots rendered from the running app |
| **Debian 12** (= the base of ParrotOS 6), x86_64 | 3.11 | the same checks |
| Debian 13, Ubuntu 24.04, x86_64 | 3.13 / 3.12 | the same checks (without the one-line job) |
| ParrotOS 6 | 3.11 | **not tested directly** – the `parrotsec/core:6` image's apt repositories lack the Debian base packages, so nothing can be installed in it; Debian 12 has the same Python and libraries |
| Kali, Linux Mint, other apt-based | 3.10 or newer | expected to work (same package names); not checked |
| Fedora, Arch, openSUSE … | 3.10 or newer | the installer prints the package names to install, then continues; not checked |
| ARM64 (aarch64) | – | PySide6 wheels for ARM need glibc ≥ 2.39 (newer than Debian 12 / Parrot 6); **not checked** |

CI runs headless (Xvfb), so it cannot tell whether the window looks right on *your* desktop, with *your* theme, scaling or Wayland compositor — please report visual problems.

**Python 3.11 note:** PySide6 6.12.0 has a reference-counting bug on Python older than 3.12 (it decrements `None` on every call and the process aborts after some minutes of use — found by this project's CI on Debian 12). `requirements.txt` therefore pins PySide6 below 6.12 on Python < 3.12, and a test (`test_pyside6_does_not_corrupt_none_refcount`) guards against it.

## Where things are

| What | Where |
|---|---|
| Program | `~/.local/opt/atlas-library` (app + venv) |
| Command / menu entry | `~/.local/bin/atlas-library`, `~/.local/share/applications/atlas-library.desktop` |
| Library data (database, index, covers, logs, backups, settings) | `~/.local/share/AtlasLibrary` (honours `XDG_DATA_HOME`, or set `ATLAS_DATA_DIR`) |
| Your books | wherever you choose; never moved or modified |

## Differences from Windows

* **External drives:** a source on a USB/other disk is remembered by the volume's UUID and its path inside the volume, so it is found again when it is mounted somewhere else (`/media/you/DISK` → `/run/media/you/DISK`). Network mounts (CIFS/SMB, NFS, sshfs) are recognised as network sources (longer timeouts, retries), and the same share mounted at two places counts as one library.
* **"Show in file manager"** opens the containing folder with `xdg-open` (it cannot pre-select the file).
* **Fonts:** the reader uses Georgia if installed, otherwise Noto Serif / DejaVu Serif / Liberation Serif.
* **OCR:** `sudo apt install tesseract-ocr tesseract-ocr-eng` (or `--with-ocr`); the app finds `tesseract` on the PATH.
* A database or backup made on Windows can be restored on Linux, but its library sources point at Windows paths and will show as offline until you add the Linux paths (books are re-linked by fingerprint when you rescan).
* The Windows installer, code-signing and PyInstaller packaging do not apply; Linux installs run from source in a venv.

## Troubleshooting

* *`Could not load the Qt platform plugin "xcb"`*: a library is missing. Re-run the installer without `--no-deps`, or run `QT_DEBUG_PLUGINS=1 atlas-library 2>&1 | grep -i "cannot\|not found"` to see which one (usually `libxcb-cursor0`).
* *Wayland oddities (blurry text, no window decorations)*: try `QT_QPA_PLATFORM=xcb atlas-library`.
* *`atlas-library: command not found`*: add `~/.local/bin` to your PATH (`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile`, then log in again).
* *`pip` cannot download packages*: the installer needs internet access once; afterwards the app is fully offline. For an offline machine, download the repository and wheels on another computer and install with `pip install --no-index --find-links`.
* Logs: `~/.local/share/AtlasLibrary/logs/atlas.log`. Self-test: `atlas-library --selftest /tmp/report.txt && cat /tmp/report.txt`.
