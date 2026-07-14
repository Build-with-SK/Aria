# Trading Intelligence System

**An institutional-grade, multi-asset trading research platform.**

> ⚠️ **Disclaimer**: This is a research and education tool. It produces probabilistic signals to assist analysis — not financial advice. No model can guarantee profit or perfectly predict markets. Always apply your own risk management and never risk more than you can afford to lose.

---

## What This System Does

- Downloads live market data for equities, indices, forex, commodities, and crypto
- Engineers 40+ technical and regime features per asset
- Generates composite buy/sell/hold signals (-100 to +100) with explanations
- Identifies market regimes (Bull, Bear, Risk-On, Risk-Off, Sideways)
- Produces risk parameters: stop-loss, take-profit, position sizing
- Displays everything in an interactive Streamlit dashboard

---

## Project Structure

```
trading-intelligence-system/
├── configs/
│   └── universe.yaml       ← Edit assets, indicators, thresholds here
├── data/
│   ├── raw/                ← Downloaded OHLCV CSVs
│   └── signals.json        ← Generated signal output
├── src/
│   ├── data/
│   │   └── data_downloader.py   ← yfinance / Bloomberg abstraction
│   ├── features/
│   │   └── indicators.py        ← All technical indicators + features
│   ├── signals/
│   │   └── signal_engine.py     ← Composite signal scoring
│   ├── dashboard/
│   │   └── dashboard.py         ← Streamlit dashboard
│   ├── models/                  ← Phase 2: ML models
│   ├── risk/                    ← Phase 2: Risk engine
│   ├── backtesting/             ← Phase 2: Backtester
│   ├── portfolio/               ← Phase 4: Portfolio optimisation
│   └── reports/                 ← Phase 3: Daily report generator
├── main.py                 ← Run this first
├── requirements.txt
└── README.md
```

---

## Installation (Windows PowerShell)

```powershell
# 1. Navigate to where you want the project
cd C:\Users\YourName\Projects

# 2. Clone or place the folder, then enter it
cd trading-intelligence-system

# 3. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the data pipeline (downloads data + generates signals)
python main.py

# 6. Launch the dashboard
streamlit run src/dashboard/dashboard.py
```

---

## How It Works

### Phase 1 Pipeline

```
configs/universe.yaml
        │
        ▼
data_downloader.py          ← Downloads OHLCV via yfinance
        │
        ▼
indicators.py               ← Computes 40+ features per asset
        │
        ▼
signal_engine.py            ← Generates composite score + explanation
        │
        ▼
data/signals.json           ← Cached signals
        │
        ▼
dashboard.py                ← Streamlit UI
```

### Signal Score Bands

| Score         | Signal       |
|---------------|--------------|
| +75 to +100   | Strong Buy   |
| +40 to +74    | Buy          |
| +10 to +39    | Mild Bullish |
| -9 to +9      | Neutral      |
| -10 to -39    | Mild Bearish |
| -40 to -74    | Sell         |
| -75 to -100   | Strong Sell  |

### Sub-score Weights

| Component   | Weight |
|-------------|--------|
| Trend       | 30%    |
| Momentum    | 25%    |
| Regime      | 20%    |
| Macro*      | 10%    |
| Volatility  | 10%    |
| Sentiment*  | 5%     |

*Placeholder in Phase 1. Integrated in Phase 3.

---

## Configuration

Edit `configs/universe.yaml` to:
- Add or remove tickers
- Change lookback period
- Adjust indicator parameters
- Change signal thresholds

---

## Roadmap

| Phase | Features |
|-------|----------|
| ✅ 1 | Data download, indicators, signal engine, Streamlit dashboard |
| 🔜 2 | ML models (RF, XGBoost), backtesting, risk engine |
| 🔜 3 | FRED macro data, sentiment API, political activity tracker |
| 🔜 4 | Bloomberg integration, portfolio optimisation |
| 🔜 5 | Production hardening, database, scheduled reports, alerts |

---

## Data Sources

| Source   | Status  | Used For |
|----------|---------|----------|
| yfinance | ✅ Live | Price data, fundamentals, news |
| FRED     | 🔜 Phase 3 | Macro: CPI, Fed Funds, yield curve |
| News API | 🔜 Phase 3 | Sentiment signals |
| Bloomberg | 🔜 Phase 4 | Professional-grade data |

---

*Built as a professional trading research system. Educational use. Not financial advice.*
