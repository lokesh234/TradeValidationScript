#!/usr/bin/env python3
"""The checklist over HTTP.

    uvicorn serve:app --reload
    curl -s localhost:8000/validate -H 'content-type: application/json' \
        -d '{"symbol": "NVDA", "strategy": "short", "instrument": "stock"}' | jq .verdict

A thin front end, deliberately: it parses a body, calls
:func:`tradeval.api.validate`, and serialises what comes back. Anything that
looks like a decision about a trade belongs a layer down, in ``tradeval/``,
where the command line can reach it too.

The request body is the :class:`~tradeval.api.requests.ValidationRequest`
dataclass itself rather than a model rewritten to match it, so the schema at
``/docs`` cannot drift away from what the service actually accepts.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import Body, FastAPI, HTTPException
from pydantic import ValidationError as PydanticError

from tradeval.api import ValidationError, ValidationRequest, report_to_dict, summary_to_dict, validate
from tradeval.config import Config
from tradeval.data.market import DataError

app = FastAPI(
    title="tradeval",
    summary="Grade a trade idea against the checklist for the kind of trade it is.",
    version="1.0.0",
)


def load_config() -> Config:
    """The config every request is graded against.

    Read once at import. Each request is graded against a copy, so a run cannot
    write its own benchmark or weights into the next one's terms.
    """
    path = os.environ.get("TRADEVAL_CONFIG")
    return Config.load(path) if path else Config()


CONFIG = load_config()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/strategies")
def strategies() -> List[Dict[str, str]]:
    """The trade types on offer, and what each one is for."""
    from tradeval.strategies import STRATEGIES

    return [
        {"key": key, "name": cls.name, "description": cls.description}
        for key, cls in STRATEGIES.items()
    ]


@app.post("/validate")
def validate_one(request: ValidationRequest) -> Dict[str, Any]:
    """Grade one trade."""
    return report_to_dict(_run(request))


@app.post("/validate/batch")
def validate_many(
    requests: List[ValidationRequest] = Body(..., embed=False),
) -> Dict[str, Any]:
    """Grade several trades, reporting per-symbol failures rather than giving up.

    One symbol that cannot be fetched should not cost the caller the others --
    the CLI takes the same view, printing what it could grade and counting the
    rest as failures.
    """
    reports, failures = [], []
    for request in requests:
        try:
            reports.append(_run(request))
        except HTTPException as exc:
            failures.append({"symbol": request.symbol, "error": exc.detail})
    return {
        "summary": summary_to_dict(reports),
        "reports": [report_to_dict(report) for report in reports],
        "failures": failures,
    }


def _run(request: ValidationRequest):
    """Run one request, mapping the service's failures onto status codes."""
    try:
        return validate(request, CONFIG)
    except ValidationError as exc:
        # The caller described a trade that cannot be graded as asked.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataError as exc:
        # The symbol is the problem, not the request -- nothing to fetch, or
        # not enough history to grade.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PydanticError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
