"""A demo restaurant for local testing, made through the founder's onboarding tools (P3) the
same way a real one is: owner verification, the ban-risk consent, a (simulated) WhatsApp
channel, go-live, an owner invite, and a menu imported into the draft for the owner to
publish on the dashboard."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from velora.admin.menu import import_menu
from velora.admin.onboarding import Onboarding
from velora.db import TenantId

SESSION_REF = "dev-session"

# The founder's CSV format (P4). Prices in rupees.
SAMPLE_MENU_CSV = """kind,category,sku,name,price,description,group,min,max
item,Burgers,ZINGER,Zinger Burger,650,Crispy chicken fillet,,,
option,,ZINGER,Regular,0,,Size,1,1
option,,ZINGER,Large,150,,Size,,
item,Burgers,SMASH,Beef Smash Burger,890,,,,
item,Desi,BIRYANI,Chicken Biryani,550,With raita and salad,,,
item,Desi,KARAHI,Chicken Karahi (half),1400,,,,
item,Sides,FRIES,Masala Fries,300,,,,
item,Drinks,COKE,Coca-Cola 345ml,120,,,,
item,Drinks,LASSI,Sweet Lassi,250,,,,
"""


@dataclass(frozen=True)
class Demo:
    tenant: TenantId
    owner_id: uuid.UUID
    owner_link: str


async def create_demo(
    platform: Onboarding, platform_url: str, *, operator: str, magic_link_url: Callable[[str], str]
) -> Demo:
    """Create and open the demo restaurant. `magic_link_url` is `Settings.magic_link_url`."""
    tenant = platform.create_tenant(slug="demo", display_name="Demo Kitchen", order_prefix="DK")
    platform.record_verification(
        tenant, method="other", status="verified", evidence_ref="local development demo"
    )
    platform.record_consent(
        tenant,
        kind="separate_number_ban_risk",
        document_version="dev",
        document_sha256=hashlib.sha256(b"local development demo").hexdigest(),
        accepted_by_name="Demo Owner",
    )
    platform.register_channel(tenant, provider="fake", session_ref=SESSION_REF)
    platform.go_live(tenant)
    invite = platform.invite_owner(tenant, display_name="Demo Owner")
    await import_menu(platform_url, tenant, SAMPLE_MENU_CSV.encode(), operator=operator)
    return Demo(tenant, invite.staff_id, magic_link_url(invite.token.wire()))
