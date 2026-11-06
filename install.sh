#!/usr/bin/env bash
# Atlas Library - one-line installer for ParrotOS, Debian, Ubuntu, Kali and other apt-based Linux distributions.
#
#   curl -fsSL https://raw.githubusercontent.com/unupunct/Local-Ebook-Library-and-Research-Workstation/main/install.sh | bash
#
# Options (pass after "bash -s --"):  --uninstall  --purge  --no-deps  --with-ocr  --ref <tag|branch>  --help
# Environment:                         ATLAS_REF, ATLAS_PREFIX, ATLAS_BIN, ATLAS_LOCAL_SRC (install from a local checkout)
#
# What it does (nothing outside your home directory except installing system libraries with apt):
#   1. installs the system libraries Qt needs (apt, with sudo; skipped with --no-deps or when everything is present)
#   2. downloads the source from GitHub into   ~/.local/opt/atlas-library/app
#   3. creates a Python virtual environment in ~/.local/opt/atlas-library/venv and installs the Python packages
#   4. adds the command  ~/.local/bin/atlas-library  and an application-menu entry
# Your books are never touched. The library database lives in ~/.local/share/AtlasLibrary (kept on uninstall unless --purge).
set -euo pipefail

REPO="unupunct/Local-Ebook-Library-and-Research-Workstation"
REF="${ATLAS_REF:-main}"
PREFIX="${ATLAS_PREFIX:-$HOME/.local/opt/atlas-library}"
BIN_DIR="${ATLAS_BIN:-$HOME/.local/bin}"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/AtlasLibrary"
MODE=install; PURGE=0; NO_DEPS=0; WITH_OCR=0

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE=uninstall ;;
    --purge) PURGE=1 ;;
    --no-deps) NO_DEPS=1 ;;
    --with-ocr) WITH_OCR=1 ;;
    --ref) shift; REF="${1:?--ref needs a value}" ;;
    -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# ------------------------------------------------------------------ uninstall
if [ "$MODE" = uninstall ]; then
  say "Removing Atlas Library program files"
  rm -rf "$PREFIX" "$BIN_DIR/atlas-library" "$APPS_DIR/atlas-library.desktop"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  if [ "$PURGE" = 1 ]; then
    say "Deleting library data in $DATA_DIR (database, index, notes, annotations, backups)"
    rm -rf "$DATA_DIR"
  else
    say "Your library data was kept in $DATA_DIR (use --purge to delete it). Your book files were never touched."
  fi
  say "Done."; exit 0
fi

[ "$(uname -s)" = Linux ] || die "this installer is for Linux; on Windows use install.ps1 or the installer from the Releases page"
[ "$(id -u)" -ne 0 ] || warn "running as root: the app will be installed for root. Run as your normal user instead unless you mean it."

# --------------------------------------------------------- system libraries
SUDO=""
if [ "$(id -u)" -ne 0 ]; then command -v sudo >/dev/null 2>&1 && SUDO="sudo"; fi

APT_PKGS="python3 python3-venv python3-pip curl ca-certificates tar
libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0
libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libdbus-1-3 libfontconfig1 libglib2.0-0 libfreetype6 fonts-dejavu-core"
[ "$WITH_OCR" = 1 ] && APT_PKGS="$APT_PKGS tesseract-ocr tesseract-ocr-eng"

if [ "$NO_DEPS" = 0 ]; then
  if command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1; then
    MISSING=""
    for p in $APT_PKGS; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
    if [ -n "$MISSING" ]; then
      say "Installing system packages:$MISSING"
      if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
        die "root rights are needed to install:$MISSING
       Run:  su -c 'apt-get install -y$MISSING'   and then re-run this installer (or use --no-deps if they are already installed)."
      fi
      $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq || warn "apt-get update failed; trying to install anyway"
      # shellcheck disable=SC2086
      $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $MISSING
    else
      say "System packages are already present"
    fi
  else
    warn "apt-get not found. Make sure Python >= 3.10 with venv/pip and the Qt runtime libraries are installed:
  Fedora: sudo dnf install python3 python3-pip mesa-libGL libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil dbus-libs fontconfig
  Arch:   sudo pacman -S python python-pip libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil"
  fi
fi

# ------------------------------------------------------------------- python
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.10 or newer was not found (install python3)"
say "Using $($PY --version) at $(command -v "$PY")"

# ------------------------------------------------------------------- source
mkdir -p "$PREFIX" "$BIN_DIR" "$APPS_DIR"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -n "${ATLAS_LOCAL_SRC:-}" ]; then
  say "Using local source: $ATLAS_LOCAL_SRC"
  mkdir -p "$TMP/src"; (cd "$ATLAS_LOCAL_SRC" && tar --exclude=.git --exclude=.venv --exclude=dist --exclude=build --exclude=__pycache__ -cf - .) | tar -xf - -C "$TMP/src"
else
  URL="https://codeload.github.com/$REPO/tar.gz/$REF"
  say "Downloading $REPO ($REF)"
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$URL" -o "$TMP/src.tar.gz" || die "download failed: $URL"
  elif command -v wget >/dev/null 2>&1; then wget -q "$URL" -O "$TMP/src.tar.gz" || die "download failed: $URL"
  else die "curl or wget is required"; fi
  mkdir -p "$TMP/src"; tar -xzf "$TMP/src.tar.gz" -C "$TMP/src" --strip-components=1
fi
[ -f "$TMP/src/atlas/__main__.py" ] || die "the downloaded archive does not look like Atlas Library"
rm -rf "$PREFIX/app"; mv "$TMP/src" "$PREFIX/app"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PREFIX/app/atlas/__init__.py")"

# --------------------------------------------------------------- virtualenv
say "Creating the Python environment (this downloads about 150 MB the first time)"
[ -x "$PREFIX/venv/bin/python" ] || "$PY" -m venv "$PREFIX/venv" || die "could not create a virtual environment (on Debian/Parrot/Ubuntu: apt install python3-venv)"
"$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip wheel
if ! "$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$PREFIX/app/requirements.txt"; then
  die "installing the Python packages failed. On ARM (aarch64) PySide6 wheels need glibc >= 2.39; see docs/LINUX.md."
fi

# ----------------------------------------------------- launcher and menu entry
cat > "$BIN_DIR/atlas-library" <<LAUNCHER
#!/bin/sh
# generated by install.sh
export PYTHONPATH="$PREFIX/app\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PREFIX/venv/bin/python" -m atlas "\$@"
LAUNCHER
chmod +x "$BIN_DIR/atlas-library"

cat > "$APPS_DIR/atlas-library.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Atlas Library
GenericName=Ebook library and research workstation
Comment=Search, read and research your personal ebook collection offline
Exec=$BIN_DIR/atlas-library
Icon=$PREFIX/app/assets/atlas.png
Terminal=false
Categories=Office;Education;Viewer;
Keywords=ebook;pdf;epub;library;search;research;
StartupWMClass=atlas-library
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

# ------------------------------------------------------------------- verify
say "Verifying the installation"
REPORT="$TMP/selftest.txt"
if "$BIN_DIR/atlas-library" --selftest "$REPORT" && grep -q "SELFTEST OK" "$REPORT"; then
  say "Self-test passed (PDF, EPUB, MOBI, DOCX, RTF, HTML, TXT, Markdown indexed and searched)"
else
  cat "$REPORT" 2>/dev/null || true
  die "the self-test failed - see the output above"
fi
XCB="$(find "$PREFIX/venv" -name libqxcb.so 2>/dev/null | head -n 1 || true)"
if [ -n "$XCB" ] && command -v ldd >/dev/null 2>&1; then
  MISSING_LIBS="$(ldd "$XCB" 2>/dev/null | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ')"
  if [ -n "$MISSING_LIBS" ]; then
    warn "the Qt display plugin is missing libraries: $MISSING_LIBS
         The app may not start until they are installed (on Debian/Parrot/Ubuntu: re-run this installer without --no-deps)."
  fi
fi

say "Atlas Library ${VERSION:-} is installed."
echo "    Start it from the application menu (Office), or run:  atlas-library"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "    Note: $BIN_DIR is not in your PATH. Add it:  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.profile  (then log in again)";; esac
echo "    Library data: $DATA_DIR      Uninstall: curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- --uninstall"
