# Language conventions

- All code, comments, docstrings, commit messages, and documentation must be in **English**.
- French is used ONLY in `.po` translation files (`web/locale/fr/LC_MESSAGES/django.po`).
- French translations must include proper accents (é, è, ê, à, ù, ç, î, ô, etc.).
- User-facing strings in templates use `{% trans "..." %}` or `{% blocktrans %}`.
- User-facing strings in Python use `gettext()` or `gettext_lazy()`.
- The default language is English (`LANGUAGE_CODE = "en"`).