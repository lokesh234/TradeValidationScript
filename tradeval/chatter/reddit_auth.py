"""Three-legged OAuth for Reddit: authorise once in a browser, run unattended after.

Used when a two-legged "script" app is not available. You sign in once, Reddit
redirects back to a short-lived local server, and the resulting refresh token is
stored so later runs never need the browser again.
"""

from __future__ import annotations

import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from tradeval.chatter.buzz import BuzzUnavailable, RedditCredentials, TOKEN_URL
from tradeval.data.http import HttpClient, HttpError

AUTHORIZE_URL = "https://www.reddit.com/api/v1/authorize"
# 'read' is all the buzz score needs: public posts and comments.
SCOPES = "read"
CALLBACK_TIMEOUT = 300.0

SUCCESS_PAGE = """<!doctype html><meta charset="utf-8">
<title>tradeval connected</title>
<body style="font-family:system-ui;text-align:center;padding-top:15vh">
<h2>Reddit connected</h2>
<p>You can close this tab and go back to the terminal.</p>
</body>"""

FAILURE_PAGE = """<!doctype html><meta charset="utf-8">
<title>tradeval failed</title>
<body style="font-family:system-ui;text-align:center;padding-top:15vh">
<h2>Authorisation failed</h2>
<p>%s</p><p>Close this tab and check the terminal.</p>
</body>"""


@dataclass
class _Callback:
    """What Reddit handed back on the redirect."""

    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None


def _handler_factory(result: _Callback, expected_state: str):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            # Browsers also ask for /favicon.ico; ignore anything but the redirect.
            if parsed.path.rstrip("/") not in ("", "/callback".rstrip("/")):
                if "favicon" in parsed.path:
                    self.send_response(404)
                    self.end_headers()
                    return
            params = parse_qs(parsed.query)
            error = (params.get("error") or [None])[0]
            code = (params.get("code") or [None])[0]
            state = (params.get("state") or [None])[0]

            if not error and not code:
                self.send_response(400)
                self.end_headers()
                return

            if error:
                message = "Reddit returned: %s" % error
            elif state != expected_state:
                message = "State mismatch -- the response did not match this request."
                error = "state_mismatch"
            else:
                message = ""

            body = (SUCCESS_PAGE if not error else FAILURE_PAGE % message).encode()
            self.send_response(200 if not error else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

            result.code, result.state, result.error = code, state, error or None

        def log_message(self, *args):
            """Silence the default stderr access log."""

    return CallbackHandler


def authorize_url(credentials: RedditCredentials, state: str, duration: str = "permanent") -> str:
    query = {
        "client_id": credentials.client_id,
        "response_type": "code",
        "state": state,
        "redirect_uri": credentials.redirect_uri,
        "duration": duration,
        "scope": SCOPES,
    }
    return "%s?%s" % (AUTHORIZE_URL, urlencode(query))


def exchange_code(credentials: RedditCredentials, code: str) -> Dict[str, str]:
    """Swap the one-time code for tokens."""
    http = HttpClient(user_agent=credentials.user_agent, timeout=15.0)
    try:
        payload = http.post_json(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": credentials.redirect_uri,
            },
            auth=credentials.basic_auth,
        )
    except HttpError as exc:
        raise BuzzUnavailable("token exchange failed: %s" % exc) from exc
    finally:
        http.close()

    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        detail = payload.get("error") if isinstance(payload, dict) else "unexpected response"
        raise BuzzUnavailable(
            "Reddit returned no refresh token (%s). Was duration=permanent accepted?" % detail
        )
    return payload


def authorize(
    credentials: RedditCredentials,
    timeout: float = CALLBACK_TIMEOUT,
    open_browser: bool = True,
) -> str:
    """Run the browser round trip and return a refresh token.

    Spins up a one-request local server on the redirect URI's host and port,
    so the code never has to be copied out of the address bar by hand.
    """
    parsed = urlparse(credentials.redirect_uri)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BuzzUnavailable("redirect_uri must be a full URL, got %r" % credentials.redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        raise BuzzUnavailable(
            "redirect_uri must point at localhost so this tool can catch the "
            "response; got %r" % credentials.redirect_uri
        )

    state = secrets.token_urlsafe(24)
    result = _Callback()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        server = HTTPServer((parsed.hostname, port), _handler_factory(result, state))
    except OSError as exc:
        raise BuzzUnavailable(
            "could not listen on %s:%d (%s). Close whatever is using that port, "
            "or set a different redirect_uri." % (parsed.hostname, port, exc)
        ) from exc

    url = authorize_url(credentials, state)
    server.timeout = 1.0
    try:
        print("Opening your browser to authorise this app.")
        print("If it does not open, paste this into your browser:\n\n  %s\n" % url)
        if open_browser:
            webbrowser.open(url)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result.code or result.error:
                break
            # Returns after `server.timeout` if nothing arrives, so the loop
            # keeps checking the deadline instead of blocking forever.
            server.handle_request()
    except KeyboardInterrupt:
        raise BuzzUnavailable("cancelled") from None
    finally:
        server.server_close()

    if result.error:
        raise BuzzUnavailable("authorisation failed: %s" % result.error)
    if not result.code:
        raise BuzzUnavailable("timed out after %.0fs waiting for Reddit to redirect back" % timeout)
    if result.state != state:
        raise BuzzUnavailable("state mismatch -- possible cross-site request, aborting")

    return str(exchange_code(credentials, result.code)["refresh_token"])
