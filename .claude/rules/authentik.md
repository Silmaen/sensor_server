---
paths:
  - "web/accounts/**"
---

# Authentik is the identity provider

- All user account management (password, MFA, profile, account creation) is handled exclusively by Authentik via OIDC.
- NEVER add password change, password reset, account registration, or profile edit features to this app.
- The only local account is the env-defined superuser, managed via `.env` vars and Django admin.
- This app only manages the **role** assignment (guest/resident/admin) via the UserProfile model.
- The superuser account (is_superuser=True) must NEVER be modifiable from the web UI.
- Only the superuser can access the Django admin (/admin/). Do not grant is_staff to other users.