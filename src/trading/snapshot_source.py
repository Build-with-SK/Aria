"""Server-configured acquisition boundary for review-store evidence.

Bindings are installed by trusted application code, never by research requests.
The reader must acquire a fresh account snapshot from its configured transport.
This module creates no connection and provides no concrete broker credentials.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .contracts import TradeProposal, content_hash, revalidated
from .multiasset_risk import RiskInputs
from .rule_profiles import RuleProfile


@dataclass(frozen=True)
class BoundRiskSource:
    account_id: str
    provider_id: str
    profile: RuleProfile
    reader: Callable[[TradeProposal, datetime], RiskInputs]

    def acquire(self, proposal: TradeProposal, now: datetime) -> RiskInputs:
        profile = revalidated(self.profile)
        if proposal.account_id != self.account_id or profile.account_id != self.account_id:
            raise ValueError('configured account identity mismatch')
        result = revalidated(self.reader(proposal, now))
        if result.account.account_id != self.account_id:
            raise ValueError('source returned another account')
        if result.profile.profile_hash != profile.profile_hash:
            raise ValueError('source profile differs from configured account registration')
        if proposal.rule_profile_hash != profile.profile_hash:
            raise ValueError('proposal profile differs from configured account registration')
        if result.account.currency != profile.account_currency:
            raise ValueError('source account currency mismatch')
        symbol = result.specification.instrument.provider_symbol
        for pv in (result.quote.provenance, result.specification.provenance):
            if pv.provider != self.provider_id or pv.original_symbol != symbol or pv.permission != 'EXECUTION':
                raise ValueError('source provider/symbol/permission mismatch')
        calc = result.broker_calculation
        if calc is None or calc.account_id != self.account_id or calc.side != proposal.side:
            raise ValueError('account-bound broker calculation required')
        pv = calc.provenance
        if pv.provider != self.provider_id or pv.original_symbol != symbol or pv.permission != 'EXECUTION':
            raise ValueError('broker calculator source mismatch')
        return result
