#!/usr/bin/env bash
# install.sh --uninstall の別名。引数はそのまま渡されます。
#   ./uninstall.sh            ユーザーインストールを削除
#   sudo ./uninstall.sh --system   システムインストールを削除
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" --uninstall "$@"
