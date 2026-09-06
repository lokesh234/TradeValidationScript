#!/usr/bin/env python3
"""Write the HTTP front end's schema out as a file.

    .venv/bin/python scripts/swagger.py                      # JSON on stdout
    .venv/bin/python scripts/swagger.py -o openapi.json
    .venv/bin/python scripts/swagger.py -f yaml -o openapi.yaml
    .venv/bin/python scripts/swagger.py --ui swagger.html    # a page to open
    .venv/bin/python scripts/swagger.py --check openapi.json # CI: has it drifted?

``serve.py`` already serves this at ``/openapi.json``, browsable at ``/docs``.
What it cannot do is hand the schema to something that is not going to run the
server: a client generator, a reviewer on a pull request, a diff that shows a
field was dropped. This builds the same document straight from ``serve:app``
-- no port, no network, no market data fetched -- so it costs nothing to run
anywhere the package imports.

The document is OpenAPI 3.1, which is what FastAPI emits; "swagger" here is
the tooling that reads it rather than the retired 2.0 spec. Anything asking
specifically for 2.0 needs a converter downstream.

Two things are added on the way out, both of which the generated schema is
poorer for missing. The request and response examples show the bodies the
README documents, including the terminal answer. Request examples are checked
here against :class:`~tradeval.api.requests.ValidationRequest`'s own fields so
that renaming a field breaks this script rather than leaving a body in the
docs that the service would now reject. And the failure responses are declared:
``serve.py`` raises 404 and 422 by hand, with a body a generated client that
has not been told about them treats as a surprise.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict

warnings.filterwarnings("ignore", message=r".*OpenSSL.*", module="urllib3")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The one body worth showing a caller: a full trade plan, since the fields it
# leaves out are easier to imagine than the ones it never mentions.
VALIDATE_EXAMPLE = {
    "symbol": "KO",
    "strategy": "long",
    "instrument": "stock",
    "account": 50000,
    "risk": 1,
    "entry": 88.0,
    "stop": 82.0,
    "target": 104.0,
}

# A batch is the interesting case rather than the same body twice: a second
# name, traded a different way, is what the summary table is for.
BATCH_EXAMPLE = [
    {"symbol": "KO", "strategy": "long", "instrument": "stock"},
    {"symbol": "NVDA", "strategy": "earnings", "instrument": "call_spread", "contract": "250/260"},
]

# The renderer's answer is a first-class part of the response now, not an
# implementation detail callers are expected to recreate from terminal panels.
# Keep this brief: the live endpoint supplies the full report when "Try it out"
# is used, while the static example shows the output's shape at a glance.
VALIDATE_RESPONSE_EXAMPLE = {
    "symbol": "KO",
    "name": "The Coca-Cola Company",
    "strategy": {"key": "long", "name": "Long Term"},
    "price": 88.07,
    "verdict": {"label": "CAUTION", "score": 74.1, "coverage_pct": 93.5},
    "results": [{"name": "Company size", "status": "PASS", "value": "$378.93B"}],
    "terminal": "========================================\\n KO  The Coca-Cola Company  $88.07\\n...",
}
BATCH_RESPONSE_EXAMPLE = {
    "summary": [{"symbol": "KO", "label": "CAUTION", "score": 74.1}],
    "reports": [VALIDATE_RESPONSE_EXAMPLE],
    "terminal_summary": "\\n SUMMARY\\n----------------------------------------\\n  KO       CAUTION   74/100\\n",
    "failures": [],
}

# What each failure means, and the body that actually comes back with it.
# ``HTTPException(detail=str)`` serialises as ``{"detail": "..."}``, which is
# not the list-of-locations shape FastAPI's own parse failure uses -- so 422,
# which both of them can raise, is declared as either.
PLAIN_ERROR = {"$ref": "#/components/schemas/Error"}
PARSE_ERROR = {"$ref": "#/components/schemas/HTTPValidationError"}
ERRORS = {
    "404": ("No data for that symbol, or not enough history to grade it.", PLAIN_ERROR),
    "422": (
        "The request describes a trade that cannot be graded as asked, or the "
        "body could not be parsed as one.",
        {"anyOf": [PLAIN_ERROR, PARSE_ERROR]},
    ),
}

# The body every hand-raised failure returns.
ERROR_SCHEMA = {
    "title": "Error",
    "type": "object",
    "properties": {"detail": {"type": "string", "title": "Detail"}},
    "required": ["detail"],
}

# Paths grouped so the page reads in the order you would use them.
TAGS = {
    "/validate": "validation",
    "/validate/batch": "validation",
    "/strategies": "reference",
    "/health": "reference",
}

UI_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>%(title)s</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<body style="margin:0"><div id="ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({spec: %(spec)s, dom_id: "#ui", tryItOutEnabled: true});
</script>
"""


def build() -> Dict[str, Any]:
    """The schema ``serve:app`` would serve, with the notes above added."""
    from serve import app
    from tradeval.api import ValidationRequest

    spec = app.openapi()

    known = set(ValidationRequest.__dataclass_fields__)
    for body in [VALIDATE_EXAMPLE, *BATCH_EXAMPLE]:
        unknown = set(body) - known
        if unknown:
            # The request forbids unknown fields, so an example carrying one is
            # a body the service would reject: worse than no example at all.
            raise SystemExit(
                "scripts/swagger.py: example uses field(s) ValidationRequest no "
                "longer has: %s" % ", ".join(sorted(unknown))
            )

    spec["servers"] = [{"url": "http://localhost:8000", "description": "uvicorn serve:app"}]
    spec.setdefault("components", {}).setdefault("schemas", {})["Error"] = ERROR_SCHEMA

    for path, operations in spec.get("paths", {}).items():
        for operation in operations.values():
            if path in TAGS:
                operation["tags"] = [TAGS[path]]
            if "requestBody" in operation:
                example = BATCH_EXAMPLE if path == "/validate/batch" else VALIDATE_EXAMPLE
                for media in operation["requestBody"].get("content", {}).values():
                    media["example"] = example
                success = operation.setdefault("responses", {}).setdefault("200", {})
                success.setdefault("content", {}).setdefault("application/json", {})["example"] = (
                    BATCH_RESPONSE_EXAMPLE if path == "/validate/batch" else VALIDATE_RESPONSE_EXAMPLE
                )
                # FastAPI declares its own 422 for a body that fails parsing;
                # ours also covers a body that parses and still describes an
                # ungradeable trade, so the description is widened rather than
                # a second entry added.
                for status, (description, schema) in ERRORS.items():
                    response = operation.setdefault("responses", {}).setdefault(status, {})
                    response["description"] = description
                    response["content"] = {"application/json": {"schema": schema}}

    spec.setdefault("tags", []).extend(
        [
            {"name": "validation", "description": "Grade a trade, or a list of them."},
            {"name": "reference", "description": "What can be asked for, and whether it is up."},
            {
                "name": "mobile",
                "description": "Structured screens for the trade.sh discovery, preview and validation flow.",
            },
        ]
    )
    return spec


def render(spec: Dict[str, Any], fmt: str) -> str:
    if fmt == "yaml":
        try:
            import yaml
        except ImportError:  # pragma: no cover - depends on the install
            raise SystemExit(
                "scripts/swagger.py: YAML output needs PyYAML "
                "(.venv/bin/pip install pyyaml), or use -f json."
            )
        return yaml.safe_dump(spec, sort_keys=False, default_flow_style=False)
    return json.dumps(spec, indent=2, sort_keys=False) + "\n"


def ui_page(spec: Dict[str, Any], title: str) -> str:
    """A single file that renders the schema, for someone without the server.

    Swagger UI itself is loaded from a CDN -- pinning a copy of it in the repo
    to save an online reader a request is not a trade worth making -- but the
    schema is inlined, so the page describes this checkout rather than whatever
    is running on port 8000.
    """
    inline = json.dumps(spec).replace("</", "<\\/")
    return UI_TEMPLATE % {"title": title, "spec": inline}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    parser.add_argument("-f", "--format", choices=("json", "yaml"), default="json")
    parser.add_argument("--ui", type=Path, metavar="PATH", help="write a browsable HTML page")
    parser.add_argument(
        "--check",
        type=Path,
        metavar="PATH",
        help="compare against a committed schema and exit non-zero if it has drifted",
    )
    args = parser.parse_args(argv)

    spec = build()

    if args.check:
        fmt = "yaml" if args.check.suffix in (".yaml", ".yml") else "json"
        current = render(spec, fmt)
        try:
            committed = args.check.read_text()
        except FileNotFoundError:
            print("%s does not exist; run scripts/swagger.py -o %s" % (args.check, args.check), file=sys.stderr)
            return 1
        if committed == current:
            print("%s is up to date." % args.check)
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                committed.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile="%s (committed)" % args.check,
                tofile="%s (generated)" % args.check,
            )
        )
        print("\n%s is out of date; run scripts/swagger.py -o %s" % (args.check, args.check), file=sys.stderr)
        return 1

    if args.ui:
        args.ui.write_text(ui_page(spec, spec.get("info", {}).get("title", "tradeval")))
        print("wrote %s (%d endpoints)" % (args.ui, len(spec.get("paths", {}))), file=sys.stderr)

    if args.out or not args.ui:
        text = render(spec, args.format)
        if args.out:
            args.out.write_text(text)
            print("wrote %s (%d endpoints)" % (args.out, len(spec.get("paths", {}))), file=sys.stderr)
        else:
            sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
