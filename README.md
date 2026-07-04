# MetalPlay

Run Windows games on macOS using **Apple Metal** for graphics. MetalPlay orchestrates [Wine](https://www.winehq.org/), [DXMT](https://github.com/3Shain/dxmt) (Direct3D 10/11 → Metal), and optional MoltenVK (Direct3D 12 → Vulkan → Metal) into a single CLI.

## How It Works

```
┌─────────────────────────┐
│   Windows Game (.exe)   │
└───────────┬─────────────┘
            │ Direct3D 11/10 API calls
┌───────────▼─────────────┐
│  DXMT Translation Layer │  ← HLSL shaders → Metal shaders
│  (d3d11.dll / dxgi.dll) │
└───────────┬─────────────┘
            │ Apple Metal API
┌───────────▼─────────────┐
│  Apple GPU (M-series)   │
└─────────────────────────┘
            ▲
┌───────────┴─────────────┐
│  Wine (PE loader)       │  ← Runs Windows binaries on macOS
└─────────────────────────┘
```

**DXMT** translates Direct3D 10 and 11 calls directly to Metal — no OpenGL middleman. This gives significantly better performance than Wine's legacy WineD3D backend, especially on Apple Silicon.

For Direct3D 12 games, MetalPlay can use **vkd3d + MoltenVK** to route D3D12 → Vulkan → Metal.

## Requirements

- macOS Sonoma (14) or later
- Apple Silicon Mac (M1/M2/M3/M4) or Intel Mac
- **Rosetta 2** on Apple Silicon (for x86_64 Wine builds)
- A Metal-capable Wine runtime (CrossOver, or FOSS CrossOver-lineage build)
- Python 3.10+

## Quick Start

```bash
git clone https://github.com/asarrafi47/metalplay.git
cd metalplay
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"

# Option A: Graphical interface (opens in your browser)
metalplay gui

# Option B: Command line
metalplay install all          # DXMT + free Gcenx Wine (~180 MB)
metalplay bottle create gaming
metalplay run ~/Downloads/SteamSetup.exe --bottle gaming
```

Or double-click / run:
```bash
./scripts/launch-gui.sh
```

## Interfaces

MetalPlay gives you **two ways** to use it — both free:

| Interface | Command | Best for |
|-----------|---------|----------|
| **Web GUI** | `metalplay gui` | Visual setup, browsing for .exe files, status at a glance |
| **CLI** | `metalplay ...` | Scripts, power users, automation |

The web UI opens at **http://127.0.0.1:8765** and includes Quick Setup, bottle management, and game launching.

For a native desktop window (requires tkinter):
```bash
brew install python-tk@3.14   # one-time
metalplay gui --desktop
```

## Free Wine Runtimes

Both options are free — Gcenx is recommended for Metal/DXMT performance:

| Runtime | Install | Metal/DXMT | Notes |
|---------|---------|------------|-------|
| **Gcenx Wine** (recommended) | `metalplay install wine` or GUI → Quick Setup | ✓ Best | Free, DXMT-compatible, ~180 MB download |
| **Wine Stable** | `metalplay install wine --source brew` or GUI button | Partial | Homebrew cask, fallback option |

CrossOver (paid) also works if you already have it: `brew install --cask crossover`

### Original CLI-only quick start

## Commands

| Command | Description |
|---------|-------------|
| `metalplay doctor` | System readiness check |
| `metalplay install dxmt` | Download and install DXMT |
| `metalplay runtime list` | Show detected Wine installations |
| `metalplay runtime setup` | Overlay DXMT onto Wine runtimes |
| `metalplay runtime register <path>` | Register a custom Wine build |
| `metalplay bottle create <name>` | Create a new Wine bottle |
| `metalplay bottle list` | List bottles |
| `metalplay bottle config <name>` | Open winecfg |
| `metalplay run <exe> [args]` | Launch a game |
| `metalplay steam setup` | Full Windows Steam setup |
| `metalplay steam launch` | Open Steam client |
| `metalplay steam games` | List installed Steam games |
| `metalplay steam run <appid>` | Launch Steam game by App ID |
| `metalplay config show` | View settings |

### Launch Options

```bash
metalplay run game.exe --bottle gaming              # Use specific bottle
metalplay run game.exe -g dxmt                      # Force DXMT (D3D11 → Metal)
metalplay run game.exe -g moltenvk                  # D3D12 via MoltenVK → Metal
metalplay run game.exe -g wined3d                   # Legacy OpenGL fallback
metalplay run game.exe -p steam                     # Use a game profile
metalplay run game.exe --cwd "/path/to/game/dir"    # Set working directory
```

## Steam — Download & Play Windows Games

MetalPlay installs the **Windows** Steam client in a dedicated `steam` bottle. Log in, download Windows-only games from the store, and play them with DirectX routed through Metal.

```bash
metalplay steam setup          # Wine + DXMT + bottle + Steam installer
metalplay steam launch         # Open Windows Steam client
metalplay steam games          # List installed games
metalplay steam run 1245620 -g dxmt       # D3D11 → Metal
metalplay steam run 1091500 -g moltenvk   # D3D12 → Metal
metalplay steam set-graphics 1091500 moltenvk --name "Cyberpunk 2077"
```

In the GUI (`metalplay gui`), open the **Steam** tab for setup, library browsing, and one-click play.

| Flag | Games | Path |
|------|-------|------|
| `-g dxmt` | DirectX 11 | D3D11 → Metal |
| `-g moltenvk` | DirectX 12 | D3D12 → Vulkan → Metal |

## Graphics Backends

| Backend | Path to Metal | Best For |
|---------|---------------|----------|
| **dxmt** (default) | D3D10/11 → Metal directly | Most modern games (Skyrim, Palworld, etc.) |
| **dxvk** | D3D10/11 → Vulkan → MoltenVK → Metal | D3D11 titles DXMT can't handle yet (auto-downloaded on first use) |
| **moltenvk** | D3D12 → vkd3d → Vulkan → Metal | D3D12-only titles |
| **wined3d** | D3D9 → OpenGL | Legacy DirectX 9 games (Source engine, etc.) |
| **auto** | Same as dxmt | Default behavior |

Source/D3D9 titles launch through CrossOver's Wine automatically when CrossOver is
installed — the free runtime's 32-bit wined3d→GL path crashes during D3D9 device
init on Apple GL, and no free translation layer covers D3D9 on Metal today (DXVK's
d3d9 needs geometry shaders MoltenVK can't offer; wined3d's Vulkan renderer can't
compile SM3 shaders). MetalPlay handles the CrossOver session end to end: bottle
registration, Retina/DPI 1:1 fixes, and launching via `cxstart`.

## Wine Runtime Options

DXMT requires a Wine build with Metal-capable `winemac.drv` (CrossOver lineage). Options:

1. **CrossOver** (easiest) — `brew install --cask crossover`
2. **FOSS CrossOver build** — run `scripts/build-wine.sh` (~40 min compile)
3. **Wine Stable/Devel** — `brew install --cask wine-stable` (may lack full Metal support)

MetalPlay auto-detects Wine at:
- `/Applications/CrossOver.app/...`
- `/Applications/Wine Stable.app/...`
- `~/.metalplay/runtimes/wine/`

## Configuration

Settings live in `~/.metalplay/config.json`:

```json
{
  "wine_runtime": "crossover",
  "default_graphics": "dxmt",
  "default_bottle": "gaming",
  "dxmt_log_level": "warn",
  "use_rosetta": true
}
```

```bash
metalplay config set default_graphics dxmt
metalplay config set default_bottle gaming
```

## Debugging

DXMT environment variables (set automatically or via game profiles):

```bash
export DXMT_LOG_LEVEL=debug          # Verbose logging
export DXMT_LOG_PATH=/tmp/dxmt-logs  # Log file directory
export MTL_SHADER_VALIDATION=1         # Metal shader validation
export MTL_DEBUG_LAYER=1               # Metal API validation
```

## Project Structure

```
metalplay/
├── cli.py              # Command-line interface
├── config.py           # User configuration
├── paths.py            # Directory layout (~/.metalplay/)
├── runtime/
│   ├── wine.py         # Wine detection and registration
│   └── dxmt.py         # DXMT download and installation
├── bottle/
│   └── manager.py      # Wine prefix (bottle) management
├── launcher/
│   └── run.py          # Game launcher with Metal env vars
└── profiles/
    └── default.json    # Game-specific profiles
scripts/
├── setup.sh            # One-command setup
└── build-wine.sh       # Build FOSS CrossOver-lineage Wine
```

## Known Limitations

- Direct3D 12 support requires MoltenVK (not as mature as DXMT for D3D11)
- 32-bit games need WoW64-enabled Wine builds
- Anti-cheat protected games (EAC, BattlEye) generally won't work
- Some games have known DXMT compatibility issues — see [dxmt.report](https://dxmt.report)

## Credits

- [DXMT](https://github.com/3Shain/dxmt) — Direct3D to Metal translation
- [Wine](https://www.winehq.org/) / [CrossOver](https://www.codeweavers.com/) — Windows compatibility layer
- [MoltenVK](https://github.com/KhronosGroup/MoltenVK) — Vulkan to Metal

## License

MIT
