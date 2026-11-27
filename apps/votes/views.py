"""M5 (votes) HTTP views. All state-changing vote actions require POST."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.comments.models import Comment
from apps.posts.models import Post

from .services import (
    InvalidVoteError,
    VotePermissionError,
    VoteTargetNotFoundError,
    cast_vote,
    get_vote_summary,
    remove_vote,
)


def _safe_redirect_target(
    request: HttpRequest,
    target_type: str,
    target_id: int,
) -> str:
    """Resolve a safe post-vote redirect URL (next -> Referer -> fallback)."""
    allowed_hosts = {request.get_host()}
    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts=allowed_hosts,
            require_https=request.is_secure(),
        ):
            return candidate
    if target_type == "post":
        return reverse("posts:post_detail", args=[target_id])
    return "/"


def _parse_vote_value(raw_value: str | None) -> int:
    if raw_value is None:
        raise InvalidVoteError("Brak wartości głosu.")
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise InvalidVoteError("Wartość głosu musi być liczbą całkowitą (+1 lub -1).") from exc


@login_required
@require_POST
def cast_vote_view(
    request: HttpRequest,
    target_type: str,
    target_id: int,
) -> HttpResponse:
    """Handle POST request to add, change, or toggle off a vote."""
    try:
        value = _parse_vote_value(request.POST.get("value"))
        cast_vote(
            user_id=request.user.pk,
            target_type=target_type,  # type: ignore[arg-type]
            target_id=target_id,
            value=value,
        )
    except InvalidVoteError as exc:
        return HttpResponseBadRequest(str(exc))
    except VotePermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except VoteTargetNotFoundError as exc:
        return HttpResponseNotFound(str(exc))

    return redirect(_safe_redirect_target(request, target_type, target_id))


@login_required
@require_POST
def remove_vote_view(
    request: HttpRequest,
    target_type: str,
    target_id: int,
) -> HttpResponse:
    """Handle POST request to explicitly remove an active vote."""
    try:
        remove_vote(
            user_id=request.user.pk,
            target_type=target_type,  # type: ignore[arg-type]
            target_id=target_id,
        )
    except InvalidVoteError as exc:
        return HttpResponseBadRequest(str(exc))
    except VotePermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except VoteTargetNotFoundError as exc:
        return HttpResponseNotFound(str(exc))

    return redirect(_safe_redirect_target(request, target_type, target_id))


@require_GET
def preview_view(request: HttpRequest) -> HttpResponse:
    """DEBUG-only sandbox screen to test live M5 voting controls before M3 integrates them."""
    if not settings.DEBUG:
        raise Http404("Podgląd modułu M5 jest dostępny tylko w trybie DEBUG.")

    user_id = request.user.pk if request.user.is_authenticated else None
    post = (
        Post.objects.select_related("author", "category")
        .filter(is_hidden=False)
        .order_by("pk")
        .first()
    )
    post_vote = get_vote_summary("post", post.pk, user_id=user_id) if post else None

    comments_with_votes = []
    if post is not None:
        comments = (
            Comment.objects.select_related("author")
            .filter(post=post, is_hidden=False)
            .order_by("created_at")
        )
        for comment in comments:
            comments_with_votes.append(
                {
                    "comment": comment,
                    "vote": get_vote_summary("comment", comment.pk, user_id=user_id),
                }
            )

    return render(
        request,
        "votes/preview.html",
        {
            "post": post,
            "post_vote": post_vote,
            "comments_with_votes": comments_with_votes,
            "next_url": request.get_full_path(),
        },
    )
