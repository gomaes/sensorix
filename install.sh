#!/usr/bin/env bash
#
# HWMonitor for Linux installer.
#
#   ./install.sh              ユーザーにインストール (~/.local, sudo 不要)
#   sudo ./install.sh --system   システム全体にインストール (/usr/local)
#   ./install.sh --uninstall  アンインストール
#
# GNOME / KDE のアプリ一覧に「HWMonitor」として登録されます。

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="hwmonitor"
APP_NAME="HWMonitor for Linux"

MODE="user"
ACTION="install"
USE_SYSTEM_PYSIDE="auto"
PYTHON_BIN="${PYTHON:-python3}"
PREFIX=""

# ---------------------------------------------------------------- helpers ---
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m警告:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mエラー:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
使い方: ./install.sh [オプション]

  --system            システム全体にインストール (/usr/local, root 権限が必要)
  --prefix DIR        インストール先を明示 (既定: ユーザー=~/.local, system=/usr/local)
  --uninstall         アンインストールする
  --check             インストール状態とデスクトップ統合を診断する
  --system-pyside     venv を作らず、OS の python-pyside6 を使う
  --venv              必ず venv を作って PySide6 を pip/uv で入れる
  --python PATH       使用する python 実行ファイル (既定: python3)
  -h, --help          このヘルプ

例:
  ./install.sh                          # 推奨: ユーザーにインストール
  sudo pacman -S python-pyside6 && ./install.sh --system-pyside
  sudo ./install.sh --system            # 全ユーザー向け
  ./install.sh --uninstall
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --system)        MODE="system" ;;
        --prefix)        PREFIX="${2:?--prefix にはディレクトリが必要です}"; shift ;;
        --prefix=*)      PREFIX="${1#*=}" ;;
        --uninstall|--remove) ACTION="uninstall" ;;
        --check|--doctor)     ACTION="check" ;;
        --system-pyside) USE_SYSTEM_PYSIDE="yes" ;;
        --venv)          USE_SYSTEM_PYSIDE="no" ;;
        --python)        PYTHON_BIN="${2:?--python には実行ファイルが必要です}"; shift ;;
        --python=*)      PYTHON_BIN="${1#*=}" ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "不明なオプション: $1  (--help を参照)" ;;
    esac
    shift
done

# ------------------------------------------------------------ destinations ---
if [ "$MODE" = "system" ]; then
    [ "$(id -u)" -eq 0 ] || die "--system には root 権限が必要です (sudo ./install.sh --system)"
    PREFIX="${PREFIX:-/usr/local}"
    DATA_DIR="$PREFIX/share"
else
    PREFIX="${PREFIX:-$HOME/.local}"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
    # A custom --prefix keeps everything together instead of using XDG paths.
    [ "$PREFIX" != "$HOME/.local" ] && DATA_DIR="$PREFIX/share"
fi

BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/$APP_ID"
DESKTOP_DIR="$DATA_DIR/applications"
ICON_DIR="$DATA_DIR/icons/hicolor"
LAUNCHER="$BIN_DIR/$APP_ID"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"

# -------------------------------------------------------------- uninstall ---
refresh_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f "$ICON_DIR" >/dev/null 2>&1 || true
    fi
    if command -v kbuildsycoca6 >/dev/null 2>&1; then
        kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
    elif command -v kbuildsycoca5 >/dev/null 2>&1; then
        kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
    fi
}

# ------------------------------------------------------------------ check ---
ok()   { printf '  \033[1;32m OK \033[0m %s\n' "$*"; }
ng()   { printf '  \033[1;31m NG \033[0m %s\n' "$*"; }
info() { printf '       %s\n' "$*"; }

if [ "$ACTION" = "check" ]; then
    say "デスクトップ統合の診断"
    echo
    problems=0

    echo "[1] デスクトップエントリ"
    found_desktop=""
    for dir in "${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
               /usr/local/share/applications /usr/share/applications; do
        if [ -f "$dir/$APP_ID.desktop" ]; then
            found_desktop="$dir/$APP_ID.desktop"
            ok "$found_desktop"
        fi
    done
    if [ -z "$found_desktop" ]; then
        ng "$APP_ID.desktop がどこにも見つかりません → ./install.sh を実行してください"
        problems=$((problems + 1))
    elif command -v desktop-file-validate >/dev/null 2>&1; then
        if desktop-file-validate "$found_desktop" 2>&1 | grep -q .; then
            ng "desktop-file-validate が問題を報告しました:"
            desktop-file-validate "$found_desktop" 2>&1 | sed 's/^/       /'
            problems=$((problems + 1))
        else
            ok "desktop-file-validate: 問題なし"
        fi
    else
        info "desktop-file-validate なし (pacman -S desktop-file-utils で検証できます)"
    fi

    echo
    echo "[2] 実行ファイル"
    if [ -n "$found_desktop" ]; then
        exec_line="$(sed -n 's/^Exec=//p' "$found_desktop" | head -1 | awk "{print \$1}")"
        if [ -x "$exec_line" ]; then
            ok "Exec=$exec_line (実行可能)"
        else
            ng "Exec=$exec_line が実行できません → ./install.sh をやり直してください"
            problems=$((problems + 1))
        fi
        wmclass="$(sed -n 's/^StartupWMClass=//p' "$found_desktop" | head -1)"
        if [ "$wmclass" = "$APP_ID" ]; then
            ok "StartupWMClass=$wmclass (タスクバーへの固定に必要)"
        else
            ng "StartupWMClass が $APP_ID ではありません: '$wmclass'"
            problems=$((problems + 1))
        fi
    fi

    echo
    echo "[3] アイコン"
    icon_hits=0
    for dir in "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" \
               /usr/local/share/icons/hicolor /usr/share/icons/hicolor; do
        for size in 48 256 scalable; do
            for ext in png svg; do
                [ -f "$dir/${size}x${size}/apps/$APP_ID.$ext" ] && icon_hits=$((icon_hits + 1))
                [ -f "$dir/$size/apps/$APP_ID.$ext" ] && icon_hits=$((icon_hits + 1))
            done
        done
    done
    if [ "$icon_hits" -gt 0 ]; then
        ok "hicolor テーマに $icon_hits 個のアイコンを確認"
    else
        ng "アイコンが見つかりません → ./install.sh を実行してください"
        problems=$((problems + 1))
    fi

    echo
    echo "[4] デスクトップ環境"
    info "XDG_CURRENT_DESKTOP = ${XDG_CURRENT_DESKTOP:-(未設定)}"
    info "XDG_SESSION_TYPE    = ${XDG_SESSION_TYPE:-(未設定)}"
    info "XDG_DATA_HOME       = ${XDG_DATA_HOME:-(未設定 → ~/.local/share)}"
    info "XDG_DATA_DIRS       = ${XDG_DATA_DIRS:-(未設定)}"
    data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    case ":${XDG_DATA_DIRS:-}:" in
        *":$data_home:"*)
            info "(XDG_DATA_DIRS にも $data_home が含まれています)" ;;
        *)
            info "(XDG_DATA_HOME は既定で検索対象なので、含まれていなくて問題ありません)" ;;
    esac

    echo
    echo "[5] メニューキャッシュ"
    for tool in kbuildsycoca6 kbuildsycoca5 update-desktop-database gtk-update-icon-cache; do
        if command -v "$tool" >/dev/null 2>&1; then
            ok "$tool あり"
        else
            info "$tool なし"
        fi
    done

    echo
    if [ "$problems" -eq 0 ]; then
        say "問題は見つかりませんでした。"
        echo "    メニューに出ない場合は、いったんログアウト / ログインしてください。"
        echo "    KDE では次のコマンドでも再読み込みできます:"
        echo "        kbuildsycoca6 --noincremental && kquitapp6 plasmashell && kstart plasmashell"
    else
        warn "$problems 件の問題が見つかりました (上の NG を参照)"
    fi
    exit $([ "$problems" -eq 0 ] && echo 0 || echo 1)
fi

if [ "$ACTION" = "uninstall" ]; then
    say "アンインストールしています..."
    rm -f  "$LAUNCHER"
    rm -f  "$DESKTOP_FILE"
    for size in 16 22 24 32 48 64 128 256; do
        rm -f "$ICON_DIR/${size}x${size}/apps/$APP_ID.png"
    done
    rm -f "$ICON_DIR/scalable/apps/$APP_ID.svg"
    if [ -d "$LIB_DIR" ]; then
        rm -rf "$LIB_DIR"
        echo "    削除: $LIB_DIR"
    fi
    refresh_caches
    say "完了しました。"
    echo "    設定は残っています。消すには: rm -rf \"\${XDG_CONFIG_HOME:-\$HOME/.config}/hwmonitor-linux\""
    exit 0
fi

# ----------------------------------------------------------- sanity checks ---
[ -d "$SRC_DIR/hwmonitor" ] || die "$SRC_DIR に hwmonitor パッケージが見つかりません"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "$PYTHON_BIN が見つかりません (sudo pacman -S python)"

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "Python 3.9 以上が必要です (検出: $PY_VERSION)"

say "$APP_NAME をインストールします"
echo "    インストール先 : $LIB_DIR"
echo "    コマンド       : $LAUNCHER"
echo "    デスクトップ   : $DESKTOP_FILE"
echo "    Python         : $PYTHON_BIN ($PY_VERSION)"

# --------------------------------------------------------- copy the source ---
say "アプリ本体をコピーしています..."
mkdir -p "$LIB_DIR"
rm -rf "$LIB_DIR/hwmonitor"
cp -r "$SRC_DIR/hwmonitor" "$LIB_DIR/hwmonitor"
cp "$SRC_DIR/requirements.txt" "$LIB_DIR/requirements.txt"
find "$LIB_DIR/hwmonitor" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ----------------------------------------------------------- python runtime ---
if [ "$USE_SYSTEM_PYSIDE" = "auto" ]; then
    if "$PYTHON_BIN" -c 'import PySide6' >/dev/null 2>&1; then
        USE_SYSTEM_PYSIDE="yes"
        say "システムの PySide6 を検出しました。venv は作成しません。"
    else
        USE_SYSTEM_PYSIDE="no"
    fi
fi

RUNTIME_PYTHON="$PYTHON_BIN"
if [ "$USE_SYSTEM_PYSIDE" = "yes" ]; then
    "$PYTHON_BIN" -c 'import PySide6' >/dev/null 2>&1 \
        || die "$PYTHON_BIN に PySide6 がありません (sudo pacman -S python-pyside6)"
else
    say "venv を作成し PySide6 を導入しています (数分かかることがあります)..."
    rm -rf "$LIB_DIR/venv"
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PYTHON_BIN" "$LIB_DIR/venv" >/dev/null
        uv pip install --python "$LIB_DIR/venv/bin/python" -r "$LIB_DIR/requirements.txt"
    else
        "$PYTHON_BIN" -m venv "$LIB_DIR/venv" \
            || die "venv を作成できません (sudo pacman -S python が必要な場合があります)"
        "$LIB_DIR/venv/bin/python" -m pip install --upgrade --quiet pip
        "$LIB_DIR/venv/bin/python" -m pip install -r "$LIB_DIR/requirements.txt"
    fi
    RUNTIME_PYTHON="$LIB_DIR/venv/bin/python"
fi

"$RUNTIME_PYTHON" -c 'import PySide6' >/dev/null 2>&1 \
    || die "PySide6 のインストールに失敗しました"

# ---------------------------------------------------------------- launcher ---
say "起動コマンドを作成しています..."
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Generated by install.sh - do not edit; re-run the installer instead.
# RESOURCE_NAME gives the X11 window the WM_CLASS that hwmonitor.desktop
# declares in StartupWMClass, so the shell shows the right icon and groups
# the window under its launcher entry.
export RESOURCE_NAME=hwmonitor
export PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$RUNTIME_PYTHON" -m hwmonitor "\$@"
LAUNCHER_EOF
chmod 755 "$LAUNCHER"

# ------------------------------------------------------------------- icons ---
say "アイコンを配置しています..."
for size in 16 22 24 32 48 64 128 256; do
    src="$SRC_DIR/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
    if [ -f "$src" ]; then
        mkdir -p "$ICON_DIR/${size}x${size}/apps"
        install -m 644 "$src" "$ICON_DIR/${size}x${size}/apps/$APP_ID.png"
    fi
done
if [ -f "$SRC_DIR/icons/hicolor/scalable/apps/$APP_ID.svg" ]; then
    mkdir -p "$ICON_DIR/scalable/apps"
    install -m 644 "$SRC_DIR/icons/hicolor/scalable/apps/$APP_ID.svg" \
        "$ICON_DIR/scalable/apps/$APP_ID.svg"
fi

# ----------------------------------------------------------- desktop entry ---
say "デスクトップエントリを登録しています..."
mkdir -p "$DESKTOP_DIR"
sed "s|@EXEC@|$LAUNCHER|g" "$SRC_DIR/desktop/$APP_ID.desktop.in" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE" \
        || warn ".desktop の検証で警告が出ましたが、動作には影響しない可能性があります"
fi

refresh_caches

# ------------------------------------------------------------------- done ---
say "インストールが完了しました。"
echo
echo "  GNOME : アクティビティ画面で「HWMonitor」を検索"
echo "  KDE   : アプリケーションランチャー → システム → HWMonitor"
echo "  端末  : $LAUNCHER"
echo
echo "  ランチャーを右クリックすると「最前面に固定して起動」も選べます。"
echo

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        warn "$BIN_DIR が PATH に含まれていません。"
        echo "    端末から hwmonitor コマンドを使うには、~/.bashrc などに次を追加してください:"
        echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "    (GNOME / KDE のメニューからの起動は絶対パスを使うため影響しません)"
        ;;
esac

if ! command -v sensors >/dev/null 2>&1; then
    warn "lm_sensors が未導入です。マザーボードのセンサーが出ない場合は次を実行してください:"
    echo "        sudo pacman -S lm_sensors && sudo sensors-detect"
fi
