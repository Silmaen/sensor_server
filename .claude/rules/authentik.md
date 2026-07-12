---
paths:
  - "web/accounts/**"
---

# Authentik is the identity provider

- All user account management (password, MFA, profile, account creation) is handled exclusively by Authentik via OIDC.
- NEVER add password change, password reset, account registration, or profile edit features to this app.
- The only local account is the env-defined superuser, managed via `.env` vars and Django admin.
- This app only manages the **role** assignment (guest/resident/admin) via the UserProfile model.
- The `.env`-defined bootstrap superuser (`DJANGO_SUPERUSER_USERNAME`) must NEVER be
  modifiable from the web UI (neither its role nor its admin access).
- Django admin access (`is_staff` + `is_superuser`) MAY be granted to other users, but
  only by an existing superuser, from the user-management page (`/accounts/users/`).
  This is the only privileged flag manageable from the web UI; account credentials,
  MFA and profile remain Authentik-only.