"""Offline contract-driven research. No broker/network/database side effects."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,help="ResearchRequest JSON (bars and provenance required)")
    args=parser.parse_args()
    from src.research.multiasset import ResearchRequest, analyse, technical_evidence, workspace_status
    if args.input is None:
        print(json.dumps(workspace_status(now=datetime.now(timezone.utc)),indent=2))
        return
    request=ResearchRequest.model_validate_json(args.input.read_text(encoding="utf-8"))
    if request.as_of>datetime.now(timezone.utc):
        parser.error("as_of cannot be in the future")
    hypothesis=analyse(request.instrument,request.bars,now=request.as_of,family=request.family,
        timeframe=request.timeframe,regime=request.regime,evidence=request.evidence)
    print(json.dumps({"hypothesis":hypothesis.model_dump(mode="json"),
        "technical":technical_evidence(request.instrument,request.bars,now=request.as_of),
        "execution_mode":"DISABLED", "order_created":False},indent=2))


if __name__=="__main__":
    main()
