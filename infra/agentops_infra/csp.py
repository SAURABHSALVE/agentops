"""The site's Content-Security-Policy, in one place.

What it protects: the sign-in token lives in the browser tab, so the worst case is script running on the page reading
it. This policy cannot stop script that is already allowed to run, but it closes the ways that script would send the
token (or anything else) away: `connect-src` allows only this origin and Cognito's hosted page, so a stolen token has
nowhere to go; nothing can frame the site, change its base URL, or load plugins.

What it cannot do: `script-src` has to allow inline scripts, because the static export of the site carries its
start-up data in inline `<script>` tags and a static file cannot hold a per-request nonce. That is why `connect-src`
and friends matter more here than `script-src`.

The browser test harness (scripts/e2e-auth-harness.mjs) serves the production build with the same directives and the
browser test fails on any violation, so a policy that would blank the real site is caught before it ships.
"""

# Directives that never change. A test checks the browser-test harness uses these exact strings.
STATIC_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
)


def content_security_policy(hosted_ui_origin: str | None) -> str:
    """The policy, allowing connections to this origin and (when sign-in is on) Cognito's hosted page."""
    connect = "connect-src 'self'" + (f" {hosted_ui_origin}" if hosted_ui_origin else "")
    return "; ".join((*STATIC_DIRECTIVES, connect))
