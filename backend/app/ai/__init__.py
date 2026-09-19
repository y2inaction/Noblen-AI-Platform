"""Noblen AI Core (Phase 2).

A provider-independent AI infrastructure layer:

    Application → AI Gateway → Provider Interface → Anthropic / OpenAI

The rest of the platform depends only on the normalized types, errors, and the
`AIGateway` — never on a vendor SDK directly.
"""
