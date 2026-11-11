# CORTEX

A desktop stock scanner and charting workstation for NASDAQ, NYSE, NSE, BSE and NSE F&O.
Built with Python and PySide6 as a personal portfolio project.

> **Not financial advice.** Read [DISCLAIMER.md](DISCLAIMER.md) before using.

## Screenshots

<!-- Add screenshots to a /screenshots folder and replace these placeholders -->
| Dashboard | Scanner |
|---|---|
| _screenshot coming soon_ | _screenshot coming soon_ |

| Charts | News |
|---|---|
| _screenshot coming soon_ | _screenshot coming soon_ |

## What it does

Cortex works like a trading terminal: one **active symbol** drives the chart, the news feed and
the dashboard; the **watchlist** on the right is always visible; the **search box** (Ctrl+K)
finds any stock by name or ticker.

**Watchlist** — multiple named lists, live quotes that refresh every minute, price-change
flashes, sorting by your order / % change / A–Z, right-click to reorder or remove. Click a
row to open it everywhere.

**Scanner** — scan your watchlist, a preset list (NIFTY 50, Nifty Bank, S&P 500 large caps,
Magnificent 7, Pharma, NSE F&O), a **custom list you type or paste**, or whole exchanges.
Tune the rules: confluence threshold, Bollinger length/deviation, MACD fast/slow/signal,
RSI period, volume-surge multiplier. Results stream in live; filter by name, minimum score or
minimum relative volume; export to CSV. **Stop** keeps what it found; **Continue** resumes
without re-scanning anything.

**Charts** — a native chart engine, no external charting library:

- 10 chart types: Candles, Hollow, Heikin Ashi, Bars, HLC, Line, Step, Area, Baseline, Renko
- 11 timeframes from 1 minute to 1 month
- 30 indicators: SMA/EMA/WMA/HMA, Bollinger, Keltner, Donchian, VWAP, Supertrend, Parabolic SAR,
  Ichimoku Cloud, Pivot Points, Volume, RSI, MACD, Stochastic, Stoch RSI, CCI, Williams %R, ROC,
  ADX, ATR, OBV, MFI, CMF
- 17 drawing tools: trend line, ray, extended line, horizontal/vertical lines, parallel channel,
  rectangle, ellipse, Fibonacci retracement and extension, measure, arrow, text note,
  long/short position with risk-reward. Click a drawing to select it, drag to move it, drag a
  handle to reshape it, press Delete (or right-click) to remove it.
- Logarithmic, percent and inverted scales, grid modes, magnet snapping
- Bar replay, compare a second symbol, crosshair legend, save as PNG

| Chart navigation | |
|---|---|
| Drag the plot | Pan in time. Drag clearly up/down to move price as well |
| Mouse wheel | Zoom time around the cursor |
| Wheel over the price axis (or Ctrl+wheel) | Stretch / squeeze the price scale |
| Drag the price axis / time axis | Stretch that axis |
| Double-click | Reset the view (on the price axis: back to auto scale) |
| Arrow keys, + / −, Home | Pan, zoom, reset |

**News** — live headlines for the active symbol, newest first, grouped by day, with sentiment
tags. Click a headline to open the article.

**Dashboard** — live index cards with sparklines, the stock in focus, watchlist movers and
breadth, top signals from your last scan, latest headlines, sector mix and scan history.

## Requirements

- Python 3.10 or newer
- Windows is the tested platform (the `.bat` helpers are Windows-only; the app itself is plain Python/Qt)

## Run from source

```bash
git clone https://github.com/your-username/cortex.git
cd cortex
python -m venv .venv
.venv\Scripts\activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python cortex_app.py
```

On Windows you can just double-click **`RUN_CORTEX.bat`**.

### Command line

```bash
python scanner.py --tickers NVDA MSFT AMD                  # watchlist scan
python scanner.py --tickers NVDA MSFT AMD --confluence 60  # relax confluence
python scanner.py --exchange NASDAQ --full-exchange        # full exchange sweep
python scanner.py --news NVDA                              # headlines
```

## Optional: Finnhub API key

Cortex works with no API key. If you want a fallback source for symbols Yahoo can't return,
get a free key at [finnhub.io](https://finnhub.io) and set it as an environment variable
(see `.env.example`):

```powershell
$env:FINNHUB_API_KEY = "your_key_here"
python cortex_app.py
```

Never commit your key — `.env` files are git-ignored.

## Build a Windows executable

```bat
pip install pyinstaller
BUILD_EXE.bat
```

Output is `dist\Cortex\Cortex.exe`, with the Cortex icon, launchable via `START_SCANNER.bat`.
The bundle excludes matplotlib, plotly and Qt WebEngine because the new interface draws its
charts natively, which keeps it far smaller and faster to start.

## Housekeeping

`CLEANUP.bat` moves generated junk into a single `_to_delete_*` folder — caches, virtual environments and build output —
without touching source code or your saved watchlist.

## Project layout

| File | Purpose |
|---|---|
| `cortex_app.py` | Views, engine threads, main window |
| `cortex_chart.py` | Native chart engine |
| `cortex_indicators.py` | Indicator maths (pure Python, no Qt) |
| `cortex_theme.py` | Colour system and shared widgets |
| `scanner.py` | Data engine: downloads, indicators, scan loop, news |
| `app.py` | The original interface |

## Data and fair use

Cortex ships no market data. It fetches from public sources at runtime on your machine
(Yahoo Finance via `yfinance`, exchange symbol lists from NSE, BSE and Nasdaq Trader,
Yahoo and Google News RSS) and caches locally in `.aether_cache/`, which is git-ignored.
You are responsible for following each provider's terms of use. Keep scan sizes and worker
counts reasonable — the data providers may rate-limit you.

## Publishing on GitHub

1. On github.com → **New repository** → name it `cortex` → **Public** → leave README/.gitignore/license **unticked** → **Create**.
2. In GitHub **Settings → Emails**, tick "Keep my email addresses private" and copy your `<id>+<username>@users.noreply.github.com` address.
3. Open a terminal inside the `cortex-release` folder and run:

   ```
   git init
   git config user.name "Your Name"
   git config user.email "<id>+<username>@users.noreply.github.com"
   git add .
   git status          # should list ~15 files; no .aether_cache, no dist, no .venv
   git commit -m "Initial release"
   git branch -M main
   git remote add origin https://github.com/<username>/cortex.git
   git push -u origin main
   ```
4. **Attach the Windows EXE as a release asset** (so the repo stays small):
   - Double-click `BUILD_EXE.bat` on your laptop; it writes `dist\Cortex\Cortex.exe`.
   - Zip the whole `dist\Cortex\` folder → `Cortex-Windows.zip`.
   - On GitHub → **Releases → Draft a new release** → tag `v2.1` → upload the zip.
5. Replace `your-username` in `README.md` and in the `USER_AGENT` line of `scanner.py` with your real GitHub username, then commit and push again.
6. Never commit a real API key. `.env` files are already git-ignored.

## License

[MIT](LICENSE)
