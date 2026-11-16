"""The person a verified credential may read mirrored document lists as."""

from server.auth.jwt import AuthInfo
from server.settings import settings


def reader_subject(auth: AuthInfo | None) -> str:
    """A primary session's subject or an explicit personal token's creator, else nobody.

    The caller resolves this spelling through the directory; token ownership and an
    email-shaped subject alone prove no verified email or hosted domain.
    """
    if (
        auth is None
        or not settings.auth_actually_required()
        or auth.impersonated_by
        or auth.agent_principal
        or auth.read_user_id
    ):
        return ""
    if auth.reads_as_creator:
        return auth.creator
    if auth.session_id and not auth.creator and not auth.is_service_account:
        return auth.subject
    return ""
