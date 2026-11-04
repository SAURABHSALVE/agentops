"""The connection workflow for one signed-in user: agree, get the template, create the role, register its ARN.

Nothing here calls AWS. Whether the role really works is checked in a later step by assuming it. What this does decide:
who may use the feature at all, which accounts may be registered, what the user agreed to, and that the ExternalId is
made here (never chosen by the client) and stays the same for the life of the connection.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.connections.models import (
    ROLE_NAME,
    AwsConnection,
    ConnectionSettings,
    ConnectionView,
    ConsentView,
    PermissionView,
    RoleRegistration,
    SetupView,
    UnavailableReason,
)
from app.connections.permissions import PERMISSIONS
from app.connections.store import ConnectionStore
from app.connections.template import TEMPLATE_FILE, render_template
from app.errors import (
    AwsAccountNotEnabledError,
    AwsConnectionNotSetUpError,
    AwsConnectUnavailableError,
    ConsentOutOfDateError,
)
from app.identity import ANONYMOUS_OWNER

# Change this whenever the wording of `consent_points` changes in substance: everyone then has to agree again.
CONSENT_VERSION = "2026-11-02"

_UNAVAILABLE_MESSAGE = {
    "not_configured": "Connecting an AWS account is not switched on for this site.",
    "sign_in_off": "Connecting an AWS account needs sign-in, which is off for this site.",
    "sign_in_required": "Sign in to connect an AWS account.",
}


def consent_points(provider_kind: str, provider_label: str) -> list[str]:
    """What the user is told, in plain words, before any of their data is read. Depends on who analyses the logs."""
    if provider_kind == "openai":
        ai = (
            f"To investigate, short excerpts of those logs and alarm texts are sent to the AI service this site uses "
            f"({provider_label}). Do not list log groups that contain data you are not allowed to share with it."
        )
    else:
        ai = (
            "This site currently uses an offline rule-based assistant, so nothing you connect is sent outside "
            "AgentOps. If the site later switches to an AI service, you will be asked to agree again first."
        )
    return [
        "AgentOps will only read: alarms, metrics, recent change events (when, which service, who) and log lines from "
        "the log groups you list. It cannot change anything in your account.",
        ai,
        "Before anything is analysed, obvious secrets and personal data in log text (keys, tokens, passwords, emails, "
        "IP addresses, card numbers) are masked. Masking catches common shapes, not every possible secret.",
        "What an investigation quotes as evidence is saved with it for 7 days, visible only to you, and is removed by "
        "Delete my investigations.",
        "AgentOps stores your role's ARN, the region, the log group names you list, and a random ExternalId. You can "
        "disconnect here at any time. To remove the access completely, also delete the CloudFormation stack in your "
        "account.",
    ]


def _new_external_id() -> str:
    return "agentops-" + secrets.token_urlsafe(24)


class ConnectionService:
    def __init__(
        self,
        store: ConnectionStore,
        settings: Settings,
        *,
        sign_in_on: bool,
        provider_kind: str,
        provider_label: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._sign_in_on = sign_in_on
        self._account = settings.aws_connect_account_id
        self._principal = settings.aws_connect_principal_arn or f"arn:aws:iam::{self._account}:root"
        self._allowed = frozenset(settings.aws_connect_allowed_accounts or ((self._account,) if self._account else ()))
        self._any_account = "*" in self._allowed
        self._provider_kind = provider_kind
        self._provider_label = provider_label
        self._now = now

    # ---- who may use it ----------------------------------------------------------------------

    def _unavailable(self, owner: str) -> UnavailableReason | None:
        if not self._account:
            return "not_configured"
        if not self._sign_in_on:
            return "sign_in_off"
        if owner == ANONYMOUS_OWNER:
            return "sign_in_required"
        return None

    def available_for_anyone(self) -> bool:
        """Whether the site offers this at all (for the public config): configured, and sign-in is on."""
        return bool(self._account) and self._sign_in_on

    def _require(self, owner: str) -> None:
        reason = self._unavailable(owner)
        if reason is not None:
            raise AwsConnectUnavailableError(_UNAVAILABLE_MESSAGE[reason])

    def _consent_current(self, connection: AwsConnection) -> bool:
        return connection.consent_version == CONSENT_VERSION and connection.consent_provider == self._provider_kind

    def _saved(self, owner: str) -> AwsConnection:
        """The owner's connection, which must exist and be agreed to in the current wording."""
        connection = self._store.get(owner)
        if connection is None:
            raise AwsConnectionNotSetUpError("Choose a region and your log groups first (step 1).")
        if not self._consent_current(connection):
            raise ConsentOutOfDateError("The wording you agreed to has changed. Read it and agree again (step 1).")
        return connection

    # ---- what the API shows --------------------------------------------------------------------

    def view(self, owner: str) -> ConnectionView:
        reason = self._unavailable(owner)
        if reason is not None:
            return ConnectionView(available=False, reason=reason)
        connection = self._store.get(owner)
        shared: dict[str, Any] = {
            "available": True,
            "consent": ConsentView(
                version=CONSENT_VERSION, points=consent_points(self._provider_kind, self._provider_label)
            ),
            "permissions": [
                PermissionView(
                    id=p.id,
                    title=p.title,
                    why=p.why,
                    actions=list(p.actions),
                    applies_to=p.applies_to,
                    reads=p.reads,
                    never=p.never,
                )
                for p in PERMISSIONS
            ],
            "setup": SetupView(
                role_name=ROLE_NAME, template_file=TEMPLATE_FILE, trusted_account_id=self._principal.split(":")[4]
            ),
        }
        if connection is None:
            return ConnectionView(**shared)
        return ConnectionView(
            **shared,
            status=connection.status,
            region=connection.region,
            log_groups=list(connection.log_groups),
            role_arn=connection.role_arn,
            external_id=connection.external_id,
            consent_current=self._consent_current(connection),
            updated_at=connection.updated_at,
            verified_at=connection.verified_at,
        )

    # ---- the steps ----------------------------------------------------------------------------

    def save_settings(self, owner: str, request: ConnectionSettings) -> ConnectionView:
        """Step 1. Changing the region or log groups means the role must be created again, so it goes back to draft."""
        self._require(owner)
        if request.consent_version != CONSENT_VERSION:
            raise ConsentOutOfDateError("The wording you agreed to has changed. Read it and agree again.")
        existing = self._store.get(owner)
        now = self._now()
        unchanged = (
            existing is not None
            and existing.region == request.region
            and set(existing.log_groups) == set(request.log_groups)
        )
        keep_consent = existing is not None and self._consent_current(existing)
        self._store.put(
            owner,
            AwsConnection(
                status=existing.status if existing is not None and unchanged else "draft",
                # Minted once: changing the settings must not change it, or the user's existing role would stop working.
                external_id=existing.external_id if existing is not None else _new_external_id(),
                region=request.region,
                log_groups=existing.log_groups if existing is not None and unchanged else tuple(request.log_groups),
                role_arn=existing.role_arn if existing is not None and unchanged else None,
                consent_version=CONSENT_VERSION,
                consent_provider=self._provider_kind,
                consented_at=existing.consented_at if existing is not None and keep_consent else now,
                updated_at=now,
                verified_at=existing.verified_at if existing is not None and unchanged else None,
            ),
        )
        return self.view(owner)

    def template(self, owner: str) -> dict[str, Any]:
        """Step 2: the template to deploy in their account."""
        self._require(owner)
        return render_template(self._saved(owner), principal_arn=self._principal)

    def register_role(self, owner: str, request: RoleRegistration) -> ConnectionView:
        """Step 3: remember the ARN of the role they created. Whether it works is proven later, by using it."""
        self._require(owner)
        connection = self._saved(owner)
        if not self._any_account and request.role_arn.split(":")[4] not in self._allowed:
            raise AwsAccountNotEnabledError("That AWS account is not enabled for connecting on this site yet.")
        registered = connection.model_copy(
            update={
                "status": "registered",
                "role_arn": request.role_arn,
                "updated_at": self._now(),
                "verified_at": None,
            }
        )
        self._store.put(owner, registered)
        return self.view(owner)

    # ---- using the connection (Phase 16B) -----------------------------------------------------------

    def feature_available(self, owner: str) -> bool:
        """Whether this owner may use the feature at all here (configured, sign-in on, and they are signed in)."""
        return self._unavailable(owner) is None

    def readable(self, owner: str) -> AwsConnection:
        """The connection to read from: switched on, agreed to in the current wording, and the role registered."""
        self._require(owner)
        connection = self._saved(owner)
        if connection.status != "registered" or connection.role_arn is None:
            raise AwsConnectionNotSetUpError("Add the ARN of the role you created first (step 3).")
        return connection

    def mark_verified(self, owner: str, connection: AwsConnection) -> None:
        """Record that the role worked just now (only if the connection is still the one that was used)."""
        current = self._store.get(owner)
        if current is not None and current.role_arn == connection.role_arn and current.region == connection.region:
            self._store.put(owner, current.model_copy(update={"verified_at": self._now()}))

    def disconnect(self, owner: str) -> ConnectionView:
        self._require(owner)
        self._store.delete(owner)
        return self.view(owner)
