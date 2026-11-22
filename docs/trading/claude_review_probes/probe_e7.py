import sys; sys.argv=[sys.argv[0], sys.argv[1]]
exec(open(sys.argv[0].replace("probe_e7.py","probes.py")).read().split("# ---- E1")[0])
from decimal import Decimal as D
# Equity 9020, STATIC overall floor 9000 => FTMO room 20. 0.10 lot, 20-pip stop => exactly $20 risk.
r = assess(*make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000", tick="0.00001",
    bid="1.08000", ask="1.08000", side="BUY", qty="0.10", entry="1.08000", sl="1.07800", tp="1.08400",
    equity="9020", balance="9020", daily_balance="9020"), now=NOW)
print("E7 exact overall floor:", r.verdict, r.monetary_risk, "equity after stop =", D("9020")-r.monetary_risk)
# E8b: same as E8 but caller reports the true daily P&L (-480) => internal daily stop
p, i = make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000", tick="0.00001",
    bid="1.08000", ask="1.08000", side="BUY", qty="0.10", entry="1.08000", sl="1.07800", tp="1.08400",
    equity="9520", balance="10000", daily_balance="10000")
print("E8 daily_net_pnl=0  :", assess(p, i, now=NOW).verdict)
print("E8 daily_net_pnl=-480:", assess(p, i.model_copy(update={"daily_net_pnl": D("-480")}), now=NOW).verdict)
