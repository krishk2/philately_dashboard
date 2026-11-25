"""In-app notifications (shown under the profile icon). The caller commits."""

from ..extensions import db
from ..models import Notification


def notify(user_id: int, message: str, link: str | None = None) -> None:
    db.session.add(Notification(user_id=user_id, message=message[:500], link=link))
