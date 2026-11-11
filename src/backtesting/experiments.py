"""Reproducibility and overlap-safe splits around ARIA's existing backtesters.

Not a new fill simulator: corporate actions, gaps and partial fills still need a
provider-specific replay before any BROKER_DEMO_TESTED/validated designation.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import random
from typing import Annotated

from pydantic import Field, model_validator

from src.trading.contracts import Contract, NonNegative, Positive, content_hash


class LabelInterval(Contract):
    row_id: str
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def interval(self):
        if self.ends_at < self.starts_at:
            raise ValueError("label end precedes start")
        return self


def chronological_split(rows: tuple[LabelInterval, ...], *, validation_at: datetime,
                        test_at: datetime, embargo: timedelta = timedelta(0)) -> dict:
    if (validation_at.tzinfo is None or test_at.tzinfo is None or
            validation_at >= test_at or embargo < timedelta(0)):
        raise ValueError("aware ordered boundaries and nonnegative embargo required")
    if tuple(sorted(rows,key=lambda r:r.starts_at)) != rows or len({r.row_id for r in rows}) != len(rows):
        raise ValueError("unique chronologically ordered rows required")
    result={"train":[],"validation":[],"test":[],"purged":[]}
    for r in rows:
        if r.starts_at < validation_at:
            key="train" if r.ends_at < validation_at-embargo else "purged"
        elif r.starts_at < test_at:
            key="validation" if r.starts_at >= validation_at+embargo and r.ends_at < test_at-embargo else "purged"
        else:
            key="test" if r.starts_at >= test_at+embargo else "purged"
        result[key].append(r.row_id)
    return result


class ExperimentManifest(Contract):
    strategy_id: str
    strategy_version: str
    code_revision: Annotated[str, Field(min_length=7)]
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    parameters_json: str
    costs_json: str
    seed: int
    created_at: datetime
    validation_status: str = "UNVERIFIED"
    assumptions: tuple[str, ...]

    @property
    def manifest_hash(self):
        return content_hash(self)


def dataset_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class RealisedTrade(Contract):
    gross_pnl: Decimal
    spread: NonNegative
    commission: NonNegative
    slippage: NonNegative
    financing: NonNegative
    initial_capital: Positive

    @property
    def net_pnl(self):
        return self.gross_pnl-self.spread-self.commission-self.slippage-self.financing


def realised_metrics(trades: tuple[RealisedTrade, ...]) -> dict:
    if not trades:
        return {"trades":0,"net_pnl":"0","win_rate":None,"profit_factor":None,
                "expectancy":None,"risk_of_ruin":None,"validation_status":"UNVERIFIED"}
    pnl=[t.net_pnl for t in trades]
    gains=sum((v for v in pnl if v>0),Decimal("0"))
    losses=-sum((v for v in pnl if v<0),Decimal("0"))
    return {"trades":len(trades),"net_pnl":str(sum(pnl)),
            "win_rate":str(Decimal(sum(v>0 for v in pnl))/len(pnl)),
            "profit_factor":str(gains/losses) if losses else None,
            "expectancy":str(sum(pnl)/len(pnl)),
            "risk_of_ruin":None,"risk_of_ruin_reason":"requires a justified distribution and ruin threshold",
            "validation_status":"UNVERIFIED"}


def block_bootstrap(pnl: tuple[Decimal, ...], *, block_size: int, samples: int=1000, seed: int=0) -> dict:
    if not pnl or not 1 <= block_size <= len(pnl) or not 1 <= samples <= 10000:
        raise ValueError("bounded sample count and valid block size required")
    if any(not x.is_finite() for x in pnl):
        raise ValueError("finite P/L required")
    rng=random.Random(seed)
    totals=[]
    for _ in range(samples):
        sequence=[]
        while len(sequence)<len(pnl):
            start=rng.randrange(len(pnl)-block_size+1)
            sequence.extend(pnl[start:start+block_size])
        totals.append(sum(sequence[:len(pnl)]))
    totals.sort()
    return {"seed":seed,"samples":samples,"block_size":block_size,
            "net_pnl_p05":str(totals[int((samples-1)*0.05)]),
            "net_pnl_p50":str(totals[int((samples-1)*0.50)]),
            "net_pnl_p95":str(totals[int((samples-1)*0.95)]),
            "assumption":"moving-block resampling of supplied historical net P/L; not future probabilities"}
