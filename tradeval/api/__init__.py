"""Calling the checklist from something other than a terminal.

``validate.py`` grew up as a command line tool, so the answers it needs arrived
as an ``argparse.Namespace`` and the questions it could not answer were put to
whoever was sitting there. Neither travels: a namespace is the shape of a
parser rather than the shape of a trade, and there is nobody at the keyboard
when the caller is an HTTP request.

This package is the same run with both of those taken out. A
:class:`~tradeval.api.requests.ValidationRequest` says what is being traded,
:func:`~tradeval.api.service.validate` grades it, and neither reads a terminal
nor writes to one. The CLI keeps its prompts -- it just asks its questions
before the run instead of during it.
"""

from tradeval.api.requests import ValidationRequest
from tradeval.api.serialize import report_to_dict, summary_to_dict
from tradeval.api.service import ValidationError, prepare, validate

__all__ = [
    "ValidationRequest",
    "ValidationError",
    "validate",
    "prepare",
    "report_to_dict",
    "summary_to_dict",
]
