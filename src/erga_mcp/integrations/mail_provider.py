from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from ..config import ErgaConfig
from ..zoho_oauth import refresh_access_token
from .gmail_live import fetch_all_inbox_metadata_with_gws
from .zoho import MailMessageMetadata
from .zoho_live import fetch_all_inbox_metadata


class MailProvider(Protocol):
    """Read bounded inbox metadata through one configured provider."""

    def fetch_inbox_metadata(
        self,
        *,
        page_size: int = 100,
        max_messages: int = 1000,
        include_content: bool = False,
        known_message_ids: Collection[str] = (),
    ) -> list[MailMessageMetadata]: ...


@dataclass(frozen=True)
class GmailMailProvider:
    gws_command: str

    def fetch_inbox_metadata(
        self,
        *,
        page_size: int = 100,
        max_messages: int = 1000,
        include_content: bool = False,
        known_message_ids: Collection[str] = (),
    ) -> list[MailMessageMetadata]:
        del include_content, known_message_ids
        return fetch_all_inbox_metadata_with_gws(
            gws_command=self.gws_command,
            page_size=page_size,
            max_messages=max_messages,
        )


@dataclass(frozen=True)
class ZohoMailProvider:
    client_id: str
    accounts_url: str
    folder: str

    def fetch_inbox_metadata(
        self,
        *,
        page_size: int = 100,
        max_messages: int = 1000,
        include_content: bool = False,
        known_message_ids: Collection[str] = (),
    ) -> list[MailMessageMetadata]:
        if not self.client_id:
            raise ValueError("mail client_id must be configured before Zoho sync")
        return fetch_all_inbox_metadata(
            access_token=refresh_access_token(
                client_id=self.client_id,
                accounts_url=self.accounts_url,
            ),
            folder=self.folder,
            page_size=page_size,
            max_messages=max_messages,
            include_content=include_content,
            known_message_ids=known_message_ids,
        )


def build_mail_provider(config: ErgaConfig) -> MailProvider:
    """Build the selected read-only mail provider without changing provider APIs."""
    if config.mail_provider == "gmail":
        return GmailMailProvider(gws_command=config.gws_command)
    return ZohoMailProvider(
        client_id=config.mail_client_id,
        accounts_url=config.mail_accounts_url,
        folder=config.mail_folder,
    )
