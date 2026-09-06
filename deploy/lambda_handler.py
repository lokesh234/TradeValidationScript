"""The service, as Lambda expects to call it.

    docker run -p 9000:8080 tradeval
    curl -s localhost:9000/2015-03-31/functions/function/invocations -d @event.json

Mangum is the whole adapter: it turns the Function URL's event into the ASGI
scope that uvicorn would have built, hands it to the same ``app`` object
``serve.py`` exports, and turns the response back. There is no second copy of
the routing, and nothing here decides anything about a trade.

The shared secret below is the one thing this file adds to the service rather
than translating for it, and it lives here rather than in ``serve.py`` on
purpose: it answers a question only the deployment has. A local
``uvicorn serve:app`` is reachable from one machine and needs no key to say so.
"""

from __future__ import annotations

import hmac
import os

# yfinance keeps a small sqlite cache of exchange timezones, and picks its
# location from the platform's user-cache directory -- ~/.cache on Linux, which
# does not exist here and could not be written if it did. Pointed at /tmp it
# behaves as it does anywhere else, with the cache living as long as the
# execution environment rather than as long as the machine. HOME goes with it
# for anything else that reaches for a home directory on the way up.
os.environ.setdefault("HOME", "/tmp")

import yfinance as yf

yf.set_tz_cache_location("/tmp/yfinance-cache")

from mangum import Mangum  # noqa: E402  -- after the cache location is set

from serve import app  # noqa: E402  -- reads config at import, so it goes last

HEADER = b"x-tradeval-key"


class RequireKey:
    """Turn away anything that does not know the shared secret.

    A Function URL with ``--auth-type NONE`` is reachable by everyone who has
    the address, and every request it accepts spends Lambda seconds and a share
    of somebody else's rate limit. This is the cheap half of the fix: one
    header, checked before the request reaches a route.

    It is ASGI rather than a FastAPI dependency or middleware so that it runs
    ahead of routing. An unknown caller should not be able to tell a real path
    from a typo, and a 404 for one and a 401 for the other tells them.

    Preflight never arrives here: the Function URL answers ``OPTIONS`` from its
    own CORS configuration without invoking the function at all, which is why
    ``x-tradeval-key`` has to be listed there as an allowed header.
    """

    def __init__(self, app, key: str):
        self.app = app
        self.key = key.encode() if key else b""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.key:
            # No secret configured. Refuse everything rather than fall open:
            # a deployment that lost its key should be an outage you notice,
            # not a public endpoint you don't.
            await self._refuse(send, 503, "Service is not configured to accept requests.")
            return

        supplied = b""
        for name, value in scope.get("headers", ()):
            if name.lower() == HEADER:
                supplied = value
                break

        # compare_digest rather than ==, so the time taken to say no does not
        # depend on how much of the key was right.
        if not hmac.compare_digest(supplied, self.key):
            await self._refuse(send, 401, "Missing or invalid x-tradeval-key header.")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(send, status: int, detail: str) -> None:
        body = ('{"detail": "%s"}' % detail).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# lifespan="off" because there is nothing to start up or shut down: the config
# is read at import and every request is graded against a copy of it. Leaving
# the protocol on would make Mangum wait for a startup event the app never
# sends.
handler = Mangum(RequireKey(app, os.environ.get("TRADEVAL_API_KEY", "")), lifespan="off")
