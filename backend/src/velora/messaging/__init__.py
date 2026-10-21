"""Provider-neutral WhatsApp messaging (P5): the provider interface, the durable inbox and
outbox, the channel state machine and work discovery.

Business code imports from here only; adapters live in `velora.messaging.providers` and are
reachable only from the composition roots (P5-T8).
"""
