# Tracking instructions

This package owns application status, contacts, reporting, projections, and client-neutral tracker,
settings, onboarding, and card presentation models.

- Persist canonical records through `store.py`; optional views are projections, not the authority.
- Keep presentation models independent of Discord and MCP SDK objects.
- Mail provider authentication and wire translation belong in `integrations/mail/`.
- Status changes must remain local unless a separate explicit remote action is authorized.
