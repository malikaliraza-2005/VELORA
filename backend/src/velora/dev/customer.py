"""A simulated WhatsApp customer for local testing.

`say` hands a typed message to the real ingress path (the same `Ingress.receive` a provider
webhook will use), as if it came from the customer's phone. The worker then handles it like
any other message, and its replies go out through the outbox to the `FakeProvider`, where
`new_replies` picks them up.
"""

from __future__ import annotations

import uuid

from velora.clock import Clock
from velora.db import Database
from velora.messaging.inbox import Ingress
from velora.messaging.provider import InboundMessage
from velora.messaging.providers.fake import FakeProvider, SentMessage


class Customer:
    def __init__(
        self, db: Database, provider: FakeProvider, clock: Clock, session_ref: str, phone: str
    ) -> None:
        self._ingress = Ingress(db, clock)
        self._provider = provider
        self._clock = clock
        self.session_ref = session_ref
        self.phone = phone
        self._seen = len(provider.sent)

    async def say(self, text: str) -> None:
        message_id = f"dev-{uuid.uuid4().hex}"
        await self._ingress.receive(
            self._provider.name,
            self.session_ref,
            [
                InboundMessage(
                    session_ref=self.session_ref,
                    dedup_key=message_id,
                    provider_message_id=message_id,
                    sender=self.phone,
                    chat_kind="direct",
                    kind="text",
                    text=text,
                    received_at=self._clock.now(),
                )
            ],
        )

    def new_replies(self) -> list[SentMessage]:
        """Every message sent since the last call, to any customer, oldest first."""
        found = self._provider.sent[self._seen :]
        self._seen += len(found)
        return found
