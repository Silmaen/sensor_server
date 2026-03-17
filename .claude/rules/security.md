---
paths:
  - "web/**/*.py"
  - "web/**/*.html"
---

# Security rules

- All forms must include `{% csrf_token %}`.
- Logout must be POST-only (`@require_POST`).
- All views require authentication via `@role_required` or `@login_required`.
- WebSocket connections must verify the user is approved (role is not None).
- User input rendered in JavaScript context must use `|escapejs`.
- Open redirects must be prevented with `url_has_allowed_host_and_scheme`.
- Query parameters (hours, days, etc.) must be bounded and validated.
- The `/healthz/` endpoint is the only unauthenticated endpoint.