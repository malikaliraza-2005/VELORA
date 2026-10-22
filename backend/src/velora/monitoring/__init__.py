"""Monitoring and alerting for the founder (P10, AD-34).

- `metrics`: a snapshot of the whole deployment, read as `velora_monitor` (counts, ages and
  states across tenants; never a payload or a customer).
- `daily`: the daily pilot metrics per restaurant (P13 needs them).
- `alerts`: rules over a snapshot, and the manager that notifies each problem once, again
  when it grows or every few hours while it lasts, and when it clears.
- `notify`: email and webhook notifiers. Alerts never go through WhatsApp, which may itself be
  the thing that is down.
- `runner`: the monitor loop; `python -m velora.monitoring` is the command line.
"""
