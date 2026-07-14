"""
dashboard.py — Phase 2 Streamlit Dashboard
Preserves all Phase 1 functionality. Adds 8-tab Phase 2 layout.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Trading Intelligence System", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.stApp{background:#0d1117;color:#e6edf3}
div[data-testid="stMetric"]{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px}
.sig-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:10px}
.sec{border-bottom:1px solid #30363d;padding-bottom:4px;margin-bottom:14px;color:#58a6ff;font-size:.9rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.bull{color:#3fb950}.bear{color:#f85149}.neut{color:#d29922}.acc{color:#58a6ff}
</style>""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _load(path):
    p = ROOT / path
    if not p.exists(): return {}
    try:
        return json.loads(p.read_text())
    except: return {}

@st.cache_data(ttl=3600)
def load_signals():     return _load("data/signals.json")
@st.cache_data(ttl=3600)
def load_futures():     return _load("data/futures_signals.json")
@st.cache_data(ttl=3600)
def load_options():     return _load("data/options_data.json")
@st.cache_data(ttl=3600)
def load_ml():          return _load("data/ml_predictions.json")
@st.cache_data(ttl=3600)
def load_backtest():    return _load("data/backtest_results.json")
@st.cache_data(ttl=3600)
def load_portfolio():   return _load("data/portfolio_analysis.json")
@st.cache_data(ttl=3600)
def load_report():      return _load("data/daily_report.json")
@st.cache_data(ttl=3600)
def load_deriv_ctx():   return _load("data/derivatives_context.json")

@st.cache_data(ttl=900)
def fetch_chart(ticker, period="1y"):
    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if hasattr(df.columns, "get_level_values"): df.columns = df.columns.get_level_values(0)
        return df
    except: return pd.DataFrame()

def action_color(a):
    return {"Strong Buy":"#238636","Buy":"#3fb950","Mild Bullish":"#56d364","Neutral":"#d29922","Mild Bearish":"#f0883e","Sell":"#da3633","Strong Sell":"#b91c1c"}.get(a,"#8b949e")

def candlestick(df, ticker):
    close = df["Close"].squeeze()
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index,open=df["Open"].squeeze(),high=df["High"].squeeze(),low=df["Low"].squeeze(),close=close,increasing_line_color="#3fb950",decreasing_line_color="#f85149",name="Price"))
    fig.add_trace(go.Scatter(x=df.index,y=close.rolling(20).mean(),line=dict(color="#58a6ff",width=1.2),name="SMA20"))
    fig.add_trace(go.Scatter(x=df.index,y=close.rolling(50).mean(),line=dict(color="#d29922",width=1.2,dash="dot"),name="SMA50"))
    fig.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#0d1117",font=dict(color="#e6edf3"),margin=dict(l=0,r=0,t=30,b=0),xaxis=dict(gridcolor="#21262d",rangeslider_visible=False),yaxis=dict(gridcolor="#21262d"),height=400,title=dict(text=f"{ticker} — Daily",x=0.02))
    return fig

def score_bar(score):
    color = "#3fb950" if score>0 else "#f85149" if score<0 else "#d29922"
    pct = abs(score)/2
    side = "left:50%" if score>0 else f"right:{50}%;left:{50-pct:.1f}%"
    return f"""<div style="background:#21262d;border-radius:4px;height:8px;width:100%;position:relative">
    <div style="position:absolute;left:50%;top:0;height:100%;width:1px;background:#8b949e;opacity:.4"></div>
    <div style="width:{pct:.1f}%;height:100%;border-radius:4px;background:{color};position:absolute;{side}"></div></div>
    <div style="font-size:.75rem;color:#8b949e;text-align:center;margin-top:2px">{score:+.1f}</div>"""

def radar(sig):
    cats = ["Trend","Momentum","Volatility","Regime","Macro*","Sentiment*","Trend"]
    vals = [(v+100)/2 for v in [sig["trend_score"],sig["momentum_score"],sig["volatility_score"],sig["regime_score"],sig["macro_score"],sig["sentiment_score"]]]
    vals.append(vals[0])
    color = "#3fb950" if sig["composite_score"]>0 else "#f85149"
    fig = go.Figure(go.Scatterpolar(r=vals,theta=cats,fill="toself",line_color=color))
    fig.update_layout(paper_bgcolor="#161b22",plot_bgcolor="#161b22",polar=dict(bgcolor="#161b22",angularaxis=dict(gridcolor="#30363d"),radialaxis=dict(visible=True,range=[0,100],gridcolor="#30363d",tickfont=dict(color="#8b949e",size=9))),font=dict(color="#e6edf3",size=10),showlegend=False,margin=dict(l=40,r=40,t=30,b=20),height=280,title=dict(text="Signal Composition (* placeholder)",font_size=10,x=0.5))
    return fig

def sigs_df(sigs):
    rows=[]
    for s in sigs:
        rows.append({"Ticker":s.get("ticker"),"Name":s.get("name"),"Class":s.get("asset_class"),"Price":f"${s.get('current_price',0):,.4f}","Score":f"{s.get('composite_score',0):+.1f}","Action":s.get("action"),"Conf":s.get("confidence"),"Bull%":f"{s.get('bullish_prob',0):.0%}","Vol":f"{s.get('realised_vol',0):.1%}","Risk":s.get("risk_level"),"Regime":s.get("regime")})
    return pd.DataFrame(rows)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Trading Intelligence")
    st.caption("Phase 2 · Research Tool · Not financial advice")
    st.markdown("---")
    signals = load_signals()
    if not signals:
        st.error("No data. Run: `python main.py`")
        st.stop()
    ticker_list = sorted(signals.keys())
    selected = st.selectbox("Select Asset", ticker_list)
    st.markdown("---")
    n_bull = sum(1 for s in signals.values() if s.get("composite_score",0)>10)
    n_bear = sum(1 for s in signals.values() if s.get("composite_score",0)<-10)
    n_neut = len(signals)-n_bull-n_bear
    c1,c2,c3 = st.columns(3)
    c1.metric("Bullish",n_bull); c2.metric("Neutral",n_neut); c3.metric("Bearish",n_bear)
    st.markdown("---")
    if st.button("🔄 Refresh",use_container_width=True):
        st.cache_data.clear(); st.rerun()
    st.markdown("---")
    st.caption("⚠️ Research tool only. Probabilistic signals. No profit guarantee.")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tabs = st.tabs(["🌍 Overview","📈 Spot Signals","📦 Futures","🎯 Options","🤖 ML Predictions","📊 Backtesting","🏦 Portfolio Risk","📋 Daily Report"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Overview (Phase 1 preserved + Phase 2 summary)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    sig = signals[selected]
    price = sig["current_price"]; score = sig["composite_score"]; action = sig["action"]
    c_left, c_right = st.columns([3,1])
    with c_left:
        st.markdown(f"## {sig['name']} &nbsp;<span style='font-size:.9rem;color:#8b949e'>{selected}</span> &nbsp;<span style='font-size:.8rem;color:#58a6ff'>{sig['asset_class'].upper()}</span>",unsafe_allow_html=True)
        st.markdown(f"<span style='font-size:2rem;font-weight:700'>${price:,.4f}</span> &nbsp;<span style='font-size:1.1rem;color:{action_color(action)};font-weight:700'>{action}</span>",unsafe_allow_html=True)
    with c_right:
        st.markdown(f"<div style='text-align:right'><div style='font-size:.75rem;color:#8b949e'>Regime</div><div style='font-size:1rem;font-weight:700;color:#58a6ff'>{sig['regime']}</div><div style='font-size:.75rem;color:#8b949e;margin-top:6px'>Risk Level</div><div style='font-size:1rem;font-weight:700'>{sig['risk_level']}</div></div>",unsafe_allow_html=True)
    st.markdown("---")
    m1,m2,m3,m4,m5,m6 = st.columns(6)
    m1.metric("Score",f"{score:+.1f}"); m2.metric("Confidence",sig["confidence"])
    m3.metric("Bull Prob",f"{sig['bullish_prob']:.0%}"); m4.metric("Bear Prob",f"{sig['bearish_prob']:.0%}")
    m5.metric("Realised Vol",f"{sig['realised_vol']:.1%}"); m6.metric("Pos Size",f"{sig['position_size_pct']:.1f}%")
    st.markdown("")
    ch1,ch2 = st.columns([3,1])
    with ch1:
        df_chart = fetch_chart(selected)
        if not df_chart.empty: st.plotly_chart(candlestick(df_chart,selected),use_container_width=True)
    with ch2:
        st.plotly_chart(radar(sig),use_container_width=True)
    st.markdown('<div class="sec">Signal Breakdown</div>',unsafe_allow_html=True)
    sc = st.columns(6)
    for col,(lbl,val) in zip(sc,[("Trend",sig["trend_score"]),("Momentum",sig["momentum_score"]),("Volatility",sig["volatility_score"]),("Regime",sig["regime_score"]),("Macro*",sig["macro_score"]),("Sentiment*",sig["sentiment_score"])]):
        with col:
            st.markdown(f"**{lbl}**")
            st.markdown(score_bar(val),unsafe_allow_html=True)
    st.markdown("")
    st.markdown('<div class="sec">Risk Parameters</div>',unsafe_allow_html=True)
    r1,r2,r3,r4 = st.columns(4)
    r1.metric("Stop-Loss",f"${sig['stop_loss']:,.4f}" if sig.get('stop_loss') else "N/A")
    r2.metric("Take-Profit",f"${sig['take_profit']:,.4f}" if sig.get('take_profit') else "N/A")
    r3.metric("Invalidation",f"${sig['invalidation']:,.4f}" if sig.get('invalidation') else "N/A")
    r4.metric("ATR %",f"{sig['atr_pct']:.2%}")
    st.markdown("")
    st.info(sig.get("explanation",""))
    ex_col,ri_col = st.columns(2)
    with ex_col:
        st.markdown("**Key Drivers**")
        for d in sig.get("drivers",[]): st.markdown(f"{'🟢' if '[Phase' not in d else '⚪'} {d}")
    with ri_col:
        st.markdown("**Key Risks**")
        for r in sig.get("risks",[]): st.markdown(f"{'🔴' if '[Phase' not in r else '⚪'} {r}")

    # Phase 2 derivatives context for selected asset
    deriv_ctx = load_deriv_ctx()
    if deriv_ctx and selected in deriv_ctx:
        ctx = deriv_ctx[selected]
        st.markdown("---")
        st.markdown('<div class="sec">Derivatives Context (Phase 2)</div>',unsafe_allow_html=True)
        d1,d2,d3 = st.columns(3)
        d1.metric("Futures Score",f"{ctx.get('futures_score',0):+.1f}","available" if ctx.get('futures_available') else "no data")
        d2.metric("Options Score",f"{ctx.get('options_score',0):+.1f}","available" if ctx.get('options_available') else "no data")
        d3.metric("Combined Adj.",f"{ctx.get('combined_adj',0):+.1f}",ctx.get('conviction_boost',''))
        st.caption(ctx.get("summary",""))

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Spot Signals table
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("### Spot Signal Table")
    all_sorted = sorted(signals.values(), key=lambda x: x.get("composite_score",0), reverse=True)
    t1,t2,t3 = st.tabs(["🟢 Top Buy","🔴 Top Sell","📋 All"])
    with t1: st.dataframe(sigs_df(all_sorted[:10]),use_container_width=True,hide_index=True)
    with t2: st.dataframe(sigs_df(list(reversed(all_sorted))[:10]),use_container_width=True,hide_index=True)
    with t3: st.dataframe(sigs_df(all_sorted),use_container_width=True,hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Futures
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("### Futures Analysis")
    futures = load_futures()
    if not futures:
        st.warning("No futures data. Run `python main.py` (full run, not --spot-only).")
    else:
        rows=[]
        for t,f in futures.items():
            rows.append({"Ticker":t,"Name":f.get("name"),"Category":f.get("category"),"Price":f"${f.get('current_price',0):,.2f}","1d Chg":f"{f.get('price_change_pct',0):.2%}","Conf.Score":f"{f.get('confirmation_score',0):+.1f}","Trend":f"{f.get('trend_score',0):+.1f}","Momentum":f"{f.get('momentum_score',0):+.1f}","Vol":f"{f.get('realised_vol',0):.1%}","Drawdown":f"{f.get('drawdown',0):.1%}","Regime":f.get("regime"),"Action":f.get("action")})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        st.markdown("---")
        st.markdown("**Select a futures contract for detail:**")
        sel_fut = st.selectbox("Futures ticker",list(futures.keys()),key="fut_sel")
        if sel_fut:
            f = futures[sel_fut]
            st.markdown(f"**{f.get('name')}** — {f.get('action','').upper()}")
            st.info(f.get("explanation",""))
            fc1,fc2,fc3,fc4 = st.columns(4)
            fc1.metric("Confirmation Score",f"{f.get('confirmation_score',0):+.1f}")
            fc2.metric("5d Return",f"{f.get('roll_ret_5d',0):.2%}")
            fc3.metric("20d Return",f"{f.get('roll_ret_20d',0):.2%}")
            fc4.metric("ATR %",f"{f.get('atr_pct',0):.2%}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Options
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("### Options Analysis")
    options = load_options()
    if not options:
        st.warning("No options data. Run `python main.py` (full run).")
    else:
        rows=[]
        for t,o in options.items():
            if not o or "error" in o: rows.append({"Ticker":t,"Status":"No data","Sentiment":"—","P/C OI":"—","IV Skew":"—","ATM Call":"—","ATM Put":"—","DTE":"—"}); continue
            rows.append({"Ticker":t,"Expiry":o.get("selected_expiry"),"DTE":o.get("days_to_expiry"),"Spot":f"${o.get('underlying_price',0):,.2f}","ATM":f"${o.get('atm_strike',0):,.2f}","P/C OI":f"{o.get('pc_oi_ratio',0):.2f}","P/C Vol":f"{o.get('pc_vol_ratio',0):.2f}","IV Skew":f"{o.get('iv_skew',0):.3f}","Call IV":f"{o.get('avg_call_iv',0):.1%}","Put IV":f"{o.get('avg_put_iv',0):.1%}","Sentiment":f"{o.get('final_sentiment_score',0):+.1f}","C Breakeven":f"${o.get('call_breakeven',0):,.2f}","P Breakeven":f"${o.get('put_breakeven',0):,.2f}"})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        st.markdown("---")
        sel_opt = st.selectbox("Options ticker",list(options.keys()),key="opt_sel")
        if sel_opt and options.get(sel_opt) and "error" not in options[sel_opt]:
            o = options[sel_opt]
            st.info(o.get("explanation",""))
            oc1,oc2,oc3,oc4,oc5 = st.columns(5)
            oc1.metric("Sentiment Score",f"{o.get('final_sentiment_score',0):+.1f}")
            oc2.metric("ATM Call Price",f"${o.get('atm_call_price',0):.2f}")
            oc3.metric("ATM Put Price",f"${o.get('atm_put_price',0):.2f}")
            oc4.metric("Max Call OI Strike",f"${o.get('top_call_strike',0):,.2f}")
            oc5.metric("Max Put OI Strike",f"${o.get('top_put_strike',0):,.2f}")
            st.markdown("**Black-Scholes Calculator**")
            bs1,bs2,bs3 = st.columns(3)
            bs_s = bs1.number_input("Spot",value=float(o.get("underlying_price",100)),step=1.0)
            bs_k = bs2.number_input("Strike",value=float(o.get("atm_strike",100)),step=1.0)
            bs_t = bs3.number_input("DTE",value=int(o.get("days_to_expiry",30)),step=1)
            bs4,bs5 = st.columns(2)
            bs_iv = bs4.number_input("IV (e.g. 0.25)",value=float(o.get("avg_call_iv",0.25)),step=0.01,format="%.3f")
            bs_r  = bs5.number_input("Risk-free rate",value=0.045,step=0.005,format="%.3f")
            if st.button("Calculate Greeks"):
                from src.derivatives.black_scholes import full_greeks
                cg = full_greeks(bs_s,bs_k,bs_t/365,bs_r,bs_iv,"call")
                pg = full_greeks(bs_s,bs_k,bs_t/365,bs_r,bs_iv,"put")
                gc1,gc2,gc3,gc4,gc5,gc6 = st.columns(6)
                gc1.metric("Call Price",f"${cg['price']:.4f}"); gc2.metric("Call Δ",f"{cg['delta']:.4f}")
                gc3.metric("Put Price",f"${pg['price']:.4f}"); gc4.metric("Put Δ",f"{pg['delta']:.4f}")
                gc5.metric("Gamma",f"{cg['gamma']:.6f}"); gc6.metric("Vega",f"{cg['vega']:.4f}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ML Predictions
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown("### ML Predictions")
    ml = load_ml()
    if not ml:
        st.warning("No ML predictions. Run `python main.py`.")
    else:
        sel_ml = st.selectbox("Select asset",sorted(ml.keys()),key="ml_sel")
        if sel_ml:
            pred = ml[sel_ml]
            if not pred.get("models_trained"):
                st.warning(pred.get("warning","No models trained for this asset."))
            else:
                pm1,pm2,pm3 = st.columns(3)
                pm1.metric("Overall Signal",pred.get("overall_signal"))
                pm2.metric("Overall Bull Prob",f"{pred.get('overall_bullish',0.5):.1%}")
                pm3.metric("Models Trained","Yes")
                st.markdown("**Prediction by Horizon**")
                for h_str,hp in pred.get("horizons",{}).items():
                    with st.expander(f"Horizon: {h_str} day(s) — {hp.get('direction')} ({hp.get('confidence')} confidence)"):
                        hc1,hc2,hc3,hc4 = st.columns(4)
                        hc1.metric("Bull Prob",f"{hp.get('bullish_prob',0):.1%}")
                        hc2.metric("Bear Prob",f"{hp.get('bearish_prob',0):.1%}")
                        hc3.metric("Model Agreement",f"{hp.get('model_agreement',0):.1%}")
                        hc4.metric("Confidence",hp.get("confidence"))
                        for m,p in hp.get("per_model_probs",{}).items():
                            st.write(f"  • {m}: bull prob = {p:.1%}")
        st.markdown("---")
        st.markdown("**All Assets — ML Summary**")
        ml_rows=[]
        for t,p in ml.items():
            ml_rows.append({"Ticker":t,"Trained":p.get("models_trained"),"Signal":p.get("overall_signal"),"Bull Prob":f"{p.get('overall_bullish',0.5):.1%}"})
        st.dataframe(pd.DataFrame(ml_rows),use_container_width=True,hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Backtesting
# ══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    st.markdown("### Backtesting Results")
    bt = load_backtest()
    if not bt:
        st.warning("No backtest data. Run `python main.py`.")
    else:
        sel_bt = st.selectbox("Select asset",sorted(bt.keys()),key="bt_sel")
        if sel_bt and sel_bt in bt:
            r = bt[sel_bt]
            if r.get("warning"): st.warning(r["warning"])
            m = r.get("metrics",{})
            bc1,bc2,bc3,bc4,bc5,bc6 = st.columns(6)
            bc1.metric("Total Return",f"{m.get('total_return',0):.1%}")
            bc2.metric("CAGR",f"{m.get('cagr',0):.1%}")
            bc3.metric("Sharpe",f"{m.get('sharpe_ratio',0):.2f}")
            bc4.metric("Sortino",f"{m.get('sortino_ratio',0):.2f}")
            bc5.metric("Max Drawdown",f"{m.get('max_drawdown',0):.1%}")
            bc6.metric("Win Rate",f"{m.get('win_rate',0):.1%}")
            bc7,bc8,bc9 = st.columns(3)
            bc7.metric("# Trades",m.get("n_trades",0))
            bc8.metric("Best Trade",f"{m.get('best_trade',0):.2%}")
            bc9.metric("Worst Trade",f"{m.get('worst_trade',0):.2%}")
            if r.get("equity_dates") and r.get("equity_curve"):
                dates = pd.to_datetime(r["equity_dates"])
                equity = pd.Series(r["equity_curve"],index=dates)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=dates,y=equity,mode="lines",name="Strategy",line=dict(color="#3fb950")))
                fig.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#0d1117",font=dict(color="#e6edf3"),xaxis=dict(gridcolor="#21262d"),yaxis=dict(gridcolor="#21262d"),height=300,margin=dict(l=0,r=0,t=20,b=0),title="Equity Curve")
                st.plotly_chart(fig,use_container_width=True)
            if r.get("trades"):
                st.markdown("**Recent Trades (last 20)**")
                trade_rows=[{"Entry":t["entry_date"],"Exit":t["exit_date"],"Dir":t["direction"],"Entry $":f"${t['entry_price']:,.4f}","Exit $":f"${t['exit_price']:,.4f}","PnL%":f"{t['pnl_pct']:.2%}","Score":t["signal_score"]} for t in r["trades"][-20:]]
                st.dataframe(pd.DataFrame(trade_rows),use_container_width=True,hide_index=True)
            if r.get("bull_metrics"):
                st.markdown("**Bull Regime Performance**")
                bm = r["bull_metrics"]
                st.write(f"Sharpe: {bm.get('sharpe_ratio',0):.2f}  |  Win Rate: {bm.get('win_rate',0):.1%}  |  Max DD: {bm.get('max_drawdown',0):.1%}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Portfolio Risk
# ══════════════════════════════════════════════════════════════════════════════
with tabs[6]:
    st.markdown("### Portfolio Risk Analysis")
    port = load_portfolio()
    if not port:
        st.warning("No portfolio data. Run `python main.py`.")
    else:
        for w in port.get("warnings",[]): st.warning(w)
        pc1,pc2,pc3,pc4 = st.columns(4)
        pc1.metric("Active Positions",port.get("n_assets_active",0))
        exp = port.get("exposure",{})
        pc2.metric("Total Allocated",f"{exp.get('total_allocated',0):.1f}%")
        pc3.metric("Cash",f"{exp.get('cash_pct',0):.1f}%")
        pc4.metric("Diversification",f"{port.get('diversification_score',0):.0f}/100")
        pc5,pc6 = st.columns(2)
        pc5.metric("Portfolio Vol (est.)",f"{port.get('portfolio_vol',0):.1%}")
        pc6.metric("Diversification Score",f"{port.get('diversification_score',0):.1f}")
        st.markdown("---")
        by_class = exp.get("by_class",{})
        if by_class:
            st.markdown("**Exposure by Asset Class**")
            fig = go.Figure(go.Bar(x=list(by_class.keys()),y=list(by_class.values()),marker_color="#58a6ff"))
            fig.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#0d1117",font=dict(color="#e6edf3"),yaxis=dict(gridcolor="#21262d",title="% of Portfolio"),height=250,margin=dict(l=0,r=0,t=20,b=0))
            st.plotly_chart(fig,use_container_width=True)
        risk_cont = port.get("risk_contribution",{})
        if risk_cont:
            st.markdown("**Risk Contribution by Asset (top 10)**")
            top10 = sorted(risk_cont.items(),key=lambda x:x[1],reverse=True)[:10]
            fig2 = go.Figure(go.Bar(x=[t for t,_ in top10],y=[c for _,c in top10],marker_color="#f85149"))
            fig2.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#0d1117",font=dict(color="#e6edf3"),yaxis=dict(gridcolor="#21262d",title="% of portfolio risk"),height=250,margin=dict(l=0,r=0,t=20,b=0))
            st.plotly_chart(fig2,use_container_width=True)
        corr = port.get("corr_matrix",{})
        if corr:
            st.markdown("**Correlation Matrix (60-day)**")
            corr_df = pd.DataFrame(corr)
            if not corr_df.empty:
                fig3 = go.Figure(go.Heatmap(z=corr_df.values,x=list(corr_df.columns),y=list(corr_df.index),colorscale="RdYlGn",zmin=-1,zmax=1))
                fig3.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#0d1117",font=dict(color="#e6edf3",size=10),height=450,margin=dict(l=0,r=0,t=20,b=0))
                st.plotly_chart(fig3,use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — Daily Report
# ══════════════════════════════════════════════════════════════════════════════
with tabs[7]:
    st.markdown("### Daily Market Intelligence Report")
    rpt = load_report()
    if not rpt:
        st.warning("No report found. Run `python main.py`.")
    else:
        st.markdown(f"**Date:** {rpt.get('date')}  |  **Regime:** `{rpt.get('market_regime')}`")
        rs = rpt.get("regime_stats",{})
        rc1,rc2,rc3 = st.columns(3)
        rc1.metric("Bullish Assets",rs.get("bull",0)); rc2.metric("Neutral",rs.get("neutral",0)); rc3.metric("Bearish",rs.get("bear",0))
        st.markdown("---")
        rep1,rep2 = st.columns(2)
        with rep1:
            st.markdown("**Top Bullish**")
            for s in rpt.get("top_bullish",[]): st.markdown(f"🟢 **{s['ticker']}** {s['action']} `{s['score']:+.1f}`")
        with rep2:
            st.markdown("**Top Bearish**")
            for s in rpt.get("top_bearish",[]): st.markdown(f"🔴 **{s['ticker']}** {s['action']} `{s['score']:+.1f}`")
        st.markdown("---")
        if rpt.get("futures_summary"):
            fs = rpt["futures_summary"]
            st.markdown(f"**Futures:** {fs.get('n_contracts',0)} contracts · Avg confirmation: `{fs.get('avg_confirmation',0):+.1f}`")
        if rpt.get("options_summary"):
            os_ = rpt["options_summary"]
            st.markdown(f"**Options:** {os_.get('n_analyzed',0)} chains · Avg sentiment: `{os_.get('avg_sentiment',0):+.1f}`")
        if rpt.get("portfolio_summary"):
            ps = rpt["portfolio_summary"]
            st.markdown(f"**Portfolio:** {ps.get('total_allocated',0):.1f}% allocated · Div score: {ps.get('diversification_score',0):.0f}/100")
            for w in ps.get("warnings",[]): st.warning(w)
        st.markdown("---")
        st.caption("⚠️ Research tool only. Not financial advice.")
