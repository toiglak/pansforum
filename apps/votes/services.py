"""M5 (votes) business services for voting on posts and comments."""

from dataclasses import dataclass
from typing import Literal, Protocol

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.accounts.services import DjangoAccessChecker
from apps.comments.models import Comment
from apps.posts.models import Post
from core.contracts import AccessChecker, ContentKind, ContentLookup, ContentRef

from .models import Vote

VALID_TARGET_TYPES: tuple[ContentKind, ...] = ("post", "comment")
VALID_VOTE_VALUES: tuple[int, ...] = (Vote.Value.UP, Vote.Value.DOWN)

VoteAction = Literal["created", "updated", "removed"]


class VoteError(Exception):
    """Base domain error for M5 voting operations."""


class InvalidVoteError(VoteError):
    """Raised when target_type or vote value is invalid."""


class VotePermissionError(VoteError):
    """Raised when the user is not allowed to vote according to AccessChecker."""


class VoteTargetNotFoundError(VoteError):
    """Raised when the target post/comment does not exist or is hidden."""


@dataclass(frozen=True, slots=True)
class VoteResult:
    """Result of casting, changing, or removing a vote."""

    target_type: ContentKind
    target_id: int
    score: int
    user_vote: int | None
    action: VoteAction


@dataclass(frozen=True, slots=True)
class VoteSummary:
    """Aggregated vote state for rendering UI controls."""

    target_type: ContentKind
    target_id: int
    score: int
    user_vote: int | None


class VoteRepository(Protocol):
    """Persistence boundary for votes (ORM in production, in-memory in unit tests)."""

    def toggle_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
        value: int,
    ) -> tuple[int | None, VoteAction]:
        """Create, update, or remove a vote atomically; return (new_user_vote, action)."""
        ...

    def delete_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> bool:
        """Delete user's active vote for the target; return True if a vote was removed."""
        ...

    def get_score(self, target_type: ContentKind, target_id: int) -> int:
        """Return the sum of all active vote values for the target."""
        ...

    def get_user_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> int | None:
        """Return user's active vote value (+1/-1) or None."""
        ...


class DjangoContentLookup:
    """ORM-backed ContentLookup until M3/M4 expose a shared implementation."""

    def get_target(self, kind: ContentKind, id: int) -> ContentRef | None:
        if kind == "post":
            row = Post.objects.filter(pk=id).values("id", "author_id", "is_hidden").first()
        elif kind == "comment":
            row = Comment.objects.filter(pk=id).values("id", "author_id", "is_hidden").first()
        else:
            return None

        if row is None:
            return None
        return ContentRef(
            kind=kind,
            id=row["id"],
            author_id=row["author_id"],
            hidden=row["is_hidden"],
        )


class DjangoVoteRepository:
    """PostgreSQL/Django ORM implementation of VoteRepository."""

    @staticmethod
    def _target_filter(target_type: ContentKind, target_id: int) -> dict[str, int]:
        if target_type == "post":
            return {"post_id": target_id}
        return {"comment_id": target_id}

    @transaction.atomic
    def toggle_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
        value: int,
    ) -> tuple[int | None, VoteAction]:
        target_filter = self._target_filter(target_type, target_id)
        existing = (
            Vote.objects.select_for_update()
            .filter(user_id=user_id, **target_filter)
            .first()
        )
        if existing is None:
            Vote.objects.create(user_id=user_id, value=value, **target_filter)
            return value, "created"

        if existing.value == value:
            existing.delete()
            return None, "removed"

        existing.value = value
        existing.save(update_fields=["value", "updated_at"])
        return value, "updated"

    @transaction.atomic
    def delete_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> bool:
        target_filter = self._target_filter(target_type, target_id)
        deleted_count, _ = Vote.objects.filter(user_id=user_id, **target_filter).delete()
        return deleted_count > 0

    def get_score(self, target_type: ContentKind, target_id: int) -> int:
        target_filter = self._target_filter(target_type, target_id)
        result = Vote.objects.filter(**target_filter).aggregate(
            total=Coalesce(Sum("value"), 0)
        )
        return int(result["total"])

    def get_user_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> int | None:
        target_filter = self._target_filter(target_type, target_id)
        return (
            Vote.objects.filter(user_id=user_id, **target_filter)
            .values_list("value", flat=True)
            .first()
        )


def _validate_target_type(target_type: str) -> ContentKind:
    if target_type not in VALID_TARGET_TYPES:
        raise InvalidVoteError(f"Nieprawidłowy typ celu głosowania: {target_type!r}.")
    return target_type  # type: ignore[return-value]


def _validate_vote_value(value: int) -> int:
    if isinstance(value, bool) or value not in VALID_VOTE_VALUES:
        raise InvalidVoteError("Wartość głosu musi wynosić +1 lub -1.")
    return value


def _ensure_can_vote(user_id: int | None, access_checker: AccessChecker) -> int:
    if user_id is None or not access_checker.has_role(user_id, "user"):
        raise VotePermissionError("Brak uprawnień do głosowania.")
    return user_id


def _ensure_target_votable(
    target_type: ContentKind,
    target_id: int,
    content_lookup: ContentLookup,
) -> ContentRef:
    target = content_lookup.get_target(target_type, target_id)
    if target is None or target.hidden:
        raise VoteTargetNotFoundError("Nie znaleziono dostępnej treści do oceny.")
    return target


def cast_vote(
    user_id: int | None,
    target_type: ContentKind,
    target_id: int,
    value: int,
    *,
    content_lookup: ContentLookup | None = None,
    access_checker: AccessChecker | None = None,
    repository: VoteRepository | None = None,
) -> VoteResult:
    """Add, change, or toggle off a user's +1/-1 vote on a post or comment."""
    validated_type = _validate_target_type(target_type)
    validated_value = _validate_vote_value(value)

    checker = access_checker or DjangoAccessChecker()
    lookup = content_lookup or DjangoContentLookup()
    repo = repository or DjangoVoteRepository()

    actor_id = _ensure_can_vote(user_id, checker)
    _ensure_target_votable(validated_type, target_id, lookup)

    new_user_vote, action = repo.toggle_vote(
        actor_id, validated_type, target_id, validated_value
    )
    score = repo.get_score(validated_type, target_id)

    return VoteResult(
        target_type=validated_type,
        target_id=target_id,
        score=score,
        user_vote=new_user_vote,
        action=action,
    )


def remove_vote(
    user_id: int | None,
    target_type: ContentKind,
    target_id: int,
    *,
    content_lookup: ContentLookup | None = None,
    access_checker: AccessChecker | None = None,
    repository: VoteRepository | None = None,
) -> VoteResult:
    """Explicitly remove a user's active vote on a post or comment."""
    validated_type = _validate_target_type(target_type)

    checker = access_checker or DjangoAccessChecker()
    lookup = content_lookup or DjangoContentLookup()
    repo = repository or DjangoVoteRepository()

    actor_id = _ensure_can_vote(user_id, checker)
    _ensure_target_votable(validated_type, target_id, lookup)

    repo.delete_vote(actor_id, validated_type, target_id)
    score = repo.get_score(validated_type, target_id)

    return VoteResult(
        target_type=validated_type,
        target_id=target_id,
        score=score,
        user_vote=None,
        action="removed",
    )


def get_vote_summary(
    target_type: ContentKind,
    target_id: int,
    user_id: int | None = None,
    *,
    repository: VoteRepository | None = None,
) -> VoteSummary:
    """Return current score and active user vote for a post or comment."""
    validated_type = _validate_target_type(target_type)
    repo = repository or DjangoVoteRepository()

    score = repo.get_score(validated_type, target_id)
    user_vote = (
        repo.get_user_vote(user_id, validated_type, target_id)
        if user_id is not None
        else None
    )
    return VoteSummary(
        target_type=validated_type,
        target_id=target_id,
        score=score,
        user_vote=user_vote,
    )


def get_vote_context(
    target_type: ContentKind,
    target_id: int,
    user_id: int | None = None,
    *,
    repository: VoteRepository | None = None,
) -> dict[str, object]:
    """Return context dict matching templates/votes/_controls.html contract."""
    summary = get_vote_summary(target_type, target_id, user_id, repository=repository)
    return {
        "target_type": summary.target_type,
        "target_id": summary.target_id,
        "score": summary.score,
        "user_vote": summary.user_vote,
    }
