# HWMonitor for Linux

Arch Linux 向けの、CPUID **HWMonitor** (Windows) によく似たハードウェアモニターです。
`/sys/class/hwmon/` を直接読み取り、チップごとのツリー表示で
**Name / Value / Min / Max / Avg** の 5 列を 1〜2 秒間隔で更新します。

![スクリーンショット](docs/screenshot.png)

## 特徴

- **依存なしで動く主経路** — `/sys/class/hwmon/hwmon*/` の `tempN_input` /
  `fanN_input` / `inN_input` などを直接読むので、root 権限もデーモンも不要です
- 各 hwmon デバイスの `name` (`k10temp`, `it8688`, `nvme`, `amdgpu` …) が
  ツリーのトップレベル項目になります
- **Min / Max / Avg** を起動時から連続的に集計 (メニューからリセット可能)
- 温度がしきい値 (既定 80 °C) を超えた行を**赤字**で表示。
  `tempN_crit` / `tempN_emergency` / `tempN_max` を公開しているチップは、
  しきい値と実機の限界値の**低いほう**で判定します
- **lm_sensors フォールバック** — hwmon で何も取れないときは自動的に
  `sensors -j` を使います (メニューから明示的に切り替えも可能)
- **GPU 対応**
  - NVIDIA: `nvidia-smi --query-gpu=...` から温度・ファン・使用率・消費電力・
    クロック・VRAM を取得
  - AMD / Intel: `/sys/class/drm/card*/device/hwmon/hwmon*/` を hwmon として読み、
    さらに `gpu_busy_percent` や VRAM 使用量を同じグループに追加
- ダークテーマ・行高 18px の高密度レイアウト
- **常に最前面に表示** (表示メニュー / `Ctrl+T`)、ウィンドウ位置と列幅を記憶
- 監視データをテキストへ保存 (`Ctrl+S`)
- センサーが 1 つも取れなくても落ちずに、原因と対処をダイアログで案内します

## 1. 依存パッケージのインストール (Arch Linux)

### 1-1. lm_sensors (推奨・フォールバック用)

hwmon の主要なドライバはカーネル同梱ですが、Super-I/O チップ (it87, nct6775 など) は
`sensors-detect` でモジュールを特定してからでないと `/sys/class/hwmon` に現れません。

```bash
sudo pacman -S lm_sensors
sudo sensors-detect          # 基本的に Enter (デフォルト) で進み、最後に YES で保存
sudo systemctl enable --now lm_sensors   # 検出したモジュールを起動時に読み込む
sensors                      # ここで値が出れば OK
```

`sensors-detect` が提案したモジュールは `/etc/modules-load.d/lm_sensors.conf` に
書き込まれます。再起動せずに試すなら手動で読み込みます。

```bash
sudo modprobe it87    # 例: マザーボードの Super-I/O チップ
```

> **メモ:** 新しめの ASUS / Gigabyte マザーでは、ACPI と競合して `it87` が
> `-EBUSY` で失敗することがあります。その場合は
> `sudo modprobe it87 ignore_resource_conflict=1` を試してください。

### 1-2. Python と GUI

```bash
sudo pacman -S python
# uv を使う場合 (推奨)
sudo pacman -S uv
```

PySide6 は AUR ではなく pip / uv からの導入で問題ありません
(Arch 公式リポジトリの `python-pyside6` を使っても構いません)。

### 1-3. GPU 用 (任意)

```bash
sudo pacman -S nvidia-utils        # NVIDIA: nvidia-smi が入ります
# AMD / Intel は追加パッケージ不要 (amdgpu / i915 が hwmon を公開します)
```

## 2. インストールと起動

### いちばん簡単な方法

```bash
cd hwmonitor
./run.sh
```

`run.sh` は初回だけ `.venv` を作って PySide6 を入れ、以後はそのまま GUI を起動します。

### uv を使う場合

```bash
cd hwmonitor
uv venv
uv pip install -r requirements.txt
uv run python -m hwmonitor
```

### pip を使う場合

```bash
cd hwmonitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m hwmonitor
```

### コマンドとして入れる場合

```bash
uv pip install -e .     # または: pip install -e .
hwmonitor
```

## 3. 使い方

### コマンドラインオプション

```
hwmonitor [-i 秒] [-t °C] [-s auto|hwmon|sensors] [--no-nvidia] [--always-on-top] [-l] [-w]
```

| オプション | 説明 |
| --- | --- |
| `-i`, `--interval SEC` | 更新間隔 (既定 1.0 秒) |
| `-t`, `--threshold °C` | 赤字にする温度のしきい値 (既定 80) |
| `-s`, `--source` | `auto` (既定) / `hwmon` / `sensors` |
| `--no-nvidia` | `nvidia-smi` を呼ばない |
| `--always-on-top` | 最前面固定で起動 |
| `-l`, `--list` | **GUI を起動せず**、検出結果を一覧表示して終了 |
| `-w`, `--watch` | **GUI を起動せず**、端末上で更新し続ける (Ctrl+C で終了) |

まず GUI なしで認識状況を確かめたいときは、これが手軽です。

```bash
./run.sh --list
```

2 秒間隔・しきい値 85 °C・最前面で起動する例:

```bash
./run.sh -i 2 -t 85 --always-on-top
```

### メニュー

| メニュー | 項目 |
| --- | --- |
| ファイル | 監視データを保存 (`Ctrl+S`) / 終了 (`Ctrl+Q`) |
| 表示 | **常に最前面に表示** (`Ctrl+T`) / すべて展開 / すべて折りたたむ |
| 計測 | Min/Max/Avg をリセット (`Ctrl+R`) / 更新間隔 / データ取得元 / 温度の警告しきい値 |
| ヘルプ | 診断情報 (取得元と警告の一覧) / バージョン情報 |

## 4. 表示される単位

| sysfs のファイル | 種類 | 表示 |
| --- | --- | --- |
| `tempN_input` | 温度 | °C (1/1000 倍して表示) |
| `fanN_input` | ファン回転数 | RPM |
| `inN_input` | 電圧 | V (1/1000 倍) |
| `currN_input` | 電流 | A (1/1000 倍) |
| `powerN_input` | 電力 | W (1/1,000,000 倍) |
| `freqN_input` | クロック | MHz |
| `energyN_input` | 電力量 | J |
| `pwmN` | ファン制御量 | % (0–255 を換算) |

センサー名は `tempN_label` があればそれを使い (例: `Tctl`, `Tccd1`, `edge`, `junction`)、
無ければ `Temp 1`, `Fan 2` のような既定名になります。

## 5. うまく動かないとき

| 症状 | 対処 |
| --- | --- |
| センサーが 1 つも出ない | `sudo sensors-detect` を実行し、`sensors` で値が出るか確認。出るなら **計測 → データ取得元 → lm_sensors のみ** に切り替え |
| マザーの温度・ファンだけ出ない | Super-I/O 用モジュール (`it87`, `nct6775`, `nct6683` など) が未ロード。`lsmod \| grep -E 'it87\|nct67'` で確認 |
| VM / コンテナで何も出ない | ホストの hwmon はゲストに見えません。実機で実行してください |
| NVIDIA GPU が出ない | `nvidia-smi` が動くか確認 (`pacman -S nvidia-utils`) |
| `PySide6 が見つかりません` | `./run.sh` を使うか、`pip install -r requirements.txt` |
| 権限エラー | hwmon は通常 root 不要です。特定ファイルだけ読めない場合、その項目は自動的にスキップされます |

エラーはウィンドウ下部のステータスバーに表示され、**ヘルプ → 診断情報** で全文を確認できます。

## 6. 開発

```bash
python3 tests/test_sensors.py     # 追加パッケージ不要
# または
uv pip install -e '.[dev]' && pytest
```

センサーの無い環境 (VM / CI) で GUI を試すには、疑似 sysfs ツリーを使えます。

```bash
python3 tests/fake_sysfs.py /tmp/fake-sys
python -m hwmonitor --hwmon-root /tmp/fake-sys/class/hwmon --drm-root /tmp/fake-sys/class/drm
```

### 構成

```
hwmonitor/
├── main.py              CLI、GUI 起動、--list / --watch
├── sensors/
│   ├── model.py         Reading / Group / Sample / Min-Max-Avg 集計
│   ├── hwmon.py         /sys/class/hwmon の読み取り (主経路) + AMD/Intel GPU
│   ├── lmsensors.py     sensors -j フォールバック
│   ├── nvidia.py        nvidia-smi
│   └── collector.py     各バックエンドの統合と例外の封じ込め
└── ui/
    ├── theme.py         ダークテーマ
    ├── worker.py        別スレッドでのセンサー取得
    └── main_window.py   QTreeWidget の 5 列表示
```

## ライセンス

MIT
