"""Test doubles (fakes) for isolated module unit tests.

Rules:
- Fakes in this module are for automated tests only.
- Production views and services in core/ and apps/ must never import from tests.fakes.
"""

from collections.abc import Iterable, Mapping

from core.contracts import ContentKind, ContentRef, PostRef, RoleName

_ROLE_LEVEL: dict[RoleName, int] = {"user": 0, "moderator": 1, "admin": 2}


class FakeCategoryCatalog:
    """In-memory fake implementing core.contracts.CategoryCatalog."""

    def __init__(self, existing_ids: Iterable[int]) -> None:
        self._existing_ids = frozenset(existing_ids)

    def category_exists(self, category_id: int) -> bool:
        return category_id in self._existing_ids


class FakeContentLookup:
    """In-memory fake implementing core.contracts.ContentLookup."""

    def __init__(self, targets: Iterable[ContentRef] = ()) -> None:
        self._targets: dict[tuple[ContentKind, int], ContentRef] = {
            (target.kind, target.id): target for target in targets
        }

    def get_target(self, kind: ContentKind, id: int) -> ContentRef | None:
        return self._targets.get((kind, id))


class FakeAccessChecker:
    """In-memory fake implementing core.contracts.AccessChecker."""

    def __init__(
        self,
        roles_by_user: Mapping[int, RoleName] | None = None,
        inactive_users: Iterable[int] = (),
    ) -> None:
        self._roles = dict(roles_by_user or {})
        self._inactive = frozenset(inactive_users)

    def is_author(self, user_id: int | None, target: PostRef | ContentRef) -> bool:
        if user_id is None or user_id in self._inactive:
            return False
        return user_id in self._roles and target.author_id == user_id

    def has_role(self, user_id: int | None, role: RoleName) -> bool:
        if user_id is None or user_id in self._inactive or role not in _ROLE_LEVEL:
            return False
        current_role = self._roles.get(user_id)
        if current_role is None:
            return False
        return _ROLE_LEVEL[current_role] >= _ROLE_LEVEL[role]
