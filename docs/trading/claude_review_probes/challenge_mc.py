"""Monte Carlo: FTMO 2-Step phase 1 ($10k, +10% target, -5% daily from midnight balance, -10% static).
Each trade risks 0.5% of initial ($50) for a 2R target; cost 0.1R ($5) round trip; 3 trades/day;
internal halt for the day after -2% ($200). Win probability p. No time limit (cap 2,000 trading days)."""
import random
def run(p, trials=20000, seed=7):
    rng = random.Random(seed); passed = failed = 0; days_pass = []
    for _ in range(trials):
        bal = 10000.0
        for day in range(2000):
            start = bal
            for _ in range(3):
                if bal - start <= -200: break
                bal += (100.0 if rng.random() < p else -50.0) - 5.0
                if bal < start - 500 or bal < 9000: break
            if bal < start - 500 or bal < 9000: failed += 1; break
            if bal >= 11000: passed += 1; days_pass.append(day + 1); break
    days_pass.sort()
    med = days_pass[len(days_pass)//2] if days_pass else None
    return passed / trials, failed / trials, med
for p in (0.30, 1/3, 0.3667, 0.40, 0.45):
    pp, pf, med = run(p)
    ev = p*100 - (1-p)*50 - 5
    print(f"win rate {p:.3f}  net EV/trade ${ev:+.2f}  P(pass)={pp:.3f}  P(fail)={pf:.3f}  median trading days to pass={med}")
