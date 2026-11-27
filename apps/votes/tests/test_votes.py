"""Unit tests (with fakes) and Django integration tests for M5 (votes)."""

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Profile
from apps.categories.models import Category
from apps.comments.models import Comment
from apps.posts.models import Post
from apps.votes.models import Vote
from apps.votes.services import (
    InvalidVoteError,
    VoteAction,
    VotePermissionError,
    VoteTargetNotFoundError,
    cast_vote,
    get_vote_context,
    get_vote_summary,
    remove_vote,
)
from core.contracts import ContentKind, ContentRef
from tests.fakes import FakeAccessChecker, FakeContentLookup

User = get_user_model()


class InMemoryVoteRepository:
    """In-memory VoteRepository test double for isolated unit tests without DB."""

    def __init__(self) -> None:
        self._votes: dict[tuple[int, ContentKind, int], int] = {}

    def toggle_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
        value: int,
    ) -> tuple[int | None, VoteAction]:
        key = (user_id, target_type, target_id)
        existing = self._votes.get(key)
        if existing is None:
            self._votes[key] = value
            return value, "created"
        if existing == value:
            del self._votes[key]
            return None, "removed"
        self._votes[key] = value
        return value, "updated"

    def delete_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> bool:
        return self._votes.pop((user_id, target_type, target_id), None) is not None

    def get_score(self, target_type: ContentKind, target_id: int) -> int:
        return sum(
            val
            for (_, kind, tid), val in self._votes.items()
            if kind == target_type and tid == target_id
        )

    def get_user_vote(
        self,
        user_id: int,
        target_type: ContentKind,
        target_id: int,
    ) -> int | None:
        return self._votes.get((user_id, target_type, target_id))

    def count_votes(self, target_type: ContentKind, target_id: int) -> int:
        return sum(
            1
            for (_, kind, tid) in self._votes
            if kind == target_type and tid == target_id
        )


class VoteServiceUnitTests(SimpleTestCase):
    """Isolated M5 service tests using FakeContentLookup and FakeAccessChecker."""

    def setUp(self) -> None:
        self.repo = InMemoryVoteRepository()
        self.lookup = FakeContentLookup(
            targets=[
                ContentRef(kind="post", id=10, author_id=1, hidden=False),
                ContentRef(kind="post", id=11, author_id=1, hidden=True),
                ContentRef(kind="comment", id=20, author_id=2, hidden=False),
                ContentRef(kind="comment", id=21, author_id=2, hidden=True),
            ]
        )
        self.access = FakeAccessChecker(
            roles_by_user={
                1: "user",
                2: "moderator",
                3: "admin",
                4: "user",
            },
            inactive_users=[4],
        )

    def test_cast_change_and_toggle_off_vote_on_post(self) -> None:
        created = cast_vote(
            user_id=1,
            target_type="post",
            target_id=10,
            value=1,
            content_lookup=self.lookup,
            access_checker=self.access,
            repository=self.repo,
        )
        self.assertEqual(created.action, "created")
        self.assertEqual(created.user_vote, 1)
        self.assertEqual(created.score, 1)
        self.assertEqual(self.repo.count_votes("post", 10), 1)

        updated = cast_vote(
            user_id=1,
            target_type="post",
            target_id=10,
            value=-1,
            content_lookup=self.lookup,
            access_checker=self.access,
            repository=self.repo,
        )
        self.assertEqual(updated.action, "updated")
        self.assertEqual(updated.user_vote, -1)
        self.assertEqual(updated.score, -1)
        self.assertEqual(self.repo.count_votes("post", 10), 1)

        removed = cast_vote(
            user_id=1,
            target_type="post",
            target_id=10,
            value=-1,
            content_lookup=self.lookup,
            access_checker=self.access,
            repository=self.repo,
        )
        self.assertEqual(removed.action, "removed")
        self.assertIsNone(removed.user_vote)
        self.assertEqual(removed.score, 0)
        self.assertEqual(self.repo.count_votes("post", 10), 0)

    def test_vote_on_comment_and_multiple_users_score_balance(self) -> None:
        cast_vote(
            1, "comment", 20, 1,
            content_lookup=self.lookup, access_checker=self.access, repository=self.repo,
        )
        cast_vote(
            2, "comment", 20, 1,
            content_lookup=self.lookup, access_checker=self.access, repository=self.repo,
        )
        result = cast_vote(
            3, "comment", 20, -1,
            content_lookup=self.lookup, access_checker=self.access, repository=self.repo,
        )
        self.assertEqual(result.score, 1)
        self.assertEqual(self.repo.count_votes("comment", 20), 3)

        summary = get_vote_summary("comment", 20, user_id=3, repository=self.repo)
        self.assertEqual(summary.score, 1)
        self.assertEqual(summary.user_vote, -1)

        anon_ctx = get_vote_context("comment", 20, user_id=None, repository=self.repo)
        self.assertEqual(
            anon_ctx,
            {"target_type": "comment", "target_id": 20, "score": 1, "user_vote": None},
        )

    def test_explicit_remove_vote(self) -> None:
        cast_vote(
            1, "post", 10, 1,
            content_lookup=self.lookup, access_checker=self.access, repository=self.repo,
        )
        removed = remove_vote(
            1, "post", 10,
            content_lookup=self.lookup, access_checker=self.access, repository=self.repo,
        )
        self.assertEqual(removed.action, "removed")
        self.assertIsNone(removed.user_vote)
        self.assertEqual(removed.score, 0)

    def test_missing_or_hidden_target_raises_not_found(self) -> None:
        for target_type, target_id in (
            ("post", 999),
            ("comment", 999),
            ("post", 11),
            ("comment", 21),
        ):
            with self.subTest(target_type=target_type, target_id=target_id):
                with self.assertRaises(VoteTargetNotFoundError):
                    cast_vote(
                        1, target_type, target_id, 1,
                        content_lookup=self.lookup,
                        access_checker=self.access,
                        repository=self.repo,
                    )

    def test_unauthorized_users_are_denied(self) -> None:
        for denied_user_id in (None, 4, 999):
            with self.subTest(user_id=denied_user_id):
                with self.assertRaises(VotePermissionError):
                    cast_vote(
                        denied_user_id, "post", 10, 1,
                        content_lookup=self.lookup,
                        access_checker=self.access,
                        repository=self.repo,
                    )

    def test_invalid_value_and_target_type_are_rejected(self) -> None:
        for bad_value in (0, 2, -2, True, False):
            with self.subTest(value=bad_value):
                with self.assertRaises(InvalidVoteError):
                    cast_vote(
                        1, "post", 10, bad_value,  # type: ignore[arg-type]
                        content_lookup=self.lookup,
                        access_checker=self.access,
                        repository=self.repo,
                    )

        with self.assertRaises(InvalidVoteError):
            cast_vote(
                1, "category", 10, 1,  # type: ignore[arg-type]
                content_lookup=self.lookup,
                access_checker=self.access,
                repository=self.repo,
            )


class VoteViewsAndUiIntegrationTests(TestCase):
    """End-to-end tests for M5 views, DB persistence, permissions, and UI partial."""

    def setUp(self) -> None:
        self.student = User.objects.create_user(username="student1", password="safe-Test-Pass-123")
        Profile.objects.create(user=self.student, role=Profile.Role.USER)

        self.other = User.objects.create_user(username="student2", password="safe-Test-Pass-123")
        Profile.objects.create(user=self.other, role=Profile.Role.USER)

        self.no_profile_user = User.objects.create_user(
            username="noprofile", password="safe-Test-Pass-123"
        )

        self.category = Category.objects.create(name="Ogłoszenia", slug="ogloszenia")
        self.post = Post.objects.create(
            author=self.student,
            category=self.category,
            title="Testowy post",
            body="Treść posta.",
        )
        self.hidden_post = Post.objects.create(
            author=self.student,
            category=self.category,
            title="Ukryty post",
            body="Treść ukryta.",
            is_hidden=True,
        )
        self.comment = Comment.objects.create(
            post=self.post,
            author=self.other,
            body="Testowy komentarz.",
        )

    def test_post_vote_create_change_and_toggle_off(self) -> None:
        self.client.force_login(self.student)
        url = reverse("votes:cast", args=["post", self.post.pk])
        post_url = reverse("posts:post_detail", args=[self.post.pk])

        # Create +1
        resp = self.client.post(url, {"value": "1", "next": post_url})
        self.assertRedirects(resp, post_url, fetch_redirect_response=False)
        self.assertEqual(Vote.objects.filter(user=self.student, post=self.post).count(), 1)
        self.assertEqual(Vote.objects.get(user=self.student, post=self.post).value, 1)

        # Change to -1 (still max 1 row)
        resp = self.client.post(url, {"value": "-1", "next": post_url})
        self.assertRedirects(resp, post_url, fetch_redirect_response=False)
        self.assertEqual(Vote.objects.filter(user=self.student, post=self.post).count(), 1)
        self.assertEqual(Vote.objects.get(user=self.student, post=self.post).value, -1)

        # Click -1 again -> toggle off (removed)
        resp = self.client.post(url, {"value": "-1", "next": post_url})
        self.assertRedirects(resp, post_url, fetch_redirect_response=False)
        self.assertFalse(Vote.objects.filter(user=self.student, post=self.post).exists())

    def test_comment_vote_and_explicit_remove(self) -> None:
        self.client.force_login(self.student)
        cast_url = reverse("votes:cast", args=["comment", self.comment.pk])
        remove_url = reverse("votes:remove", args=["comment", self.comment.pk])

        resp = self.client.post(cast_url, {"value": "1"})
        self.assertRedirects(resp, "/", fetch_redirect_response=False)
        self.assertEqual(Vote.objects.get(user=self.student, comment=self.comment).value, 1)

        resp = self.client.post(remove_url)
        self.assertRedirects(resp, "/", fetch_redirect_response=False)
        self.assertFalse(Vote.objects.filter(user=self.student, comment=self.comment).exists())

    def test_anonymous_get_and_permission_errors(self) -> None:
        url = reverse("votes:cast", args=["post", self.post.pk])

        # Anonymous user redirected to login
        resp = self.client.post(url, {"value": "1"})
        self.assertRedirects(
            resp,
            f"{reverse('accounts:login')}?next={url}",
            fetch_redirect_response=False,
        )

        # GET not allowed for logged-in user
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 405)

        # User without forum Profile role is rejected by AccessChecker with 403
        self.client.force_login(self.no_profile_user)
        self.assertEqual(self.client.post(url, {"value": "1"}).status_code, 403)

    def test_missing_hidden_and_invalid_vote_requests(self) -> None:
        self.client.force_login(self.student)

        missing_url = reverse("votes:cast", args=["post", 999999])
        self.assertEqual(self.client.post(missing_url, {"value": "1"}).status_code, 404)

        hidden_url = reverse("votes:cast", args=["post", self.hidden_post.pk])
        self.assertEqual(self.client.post(hidden_url, {"value": "1"}).status_code, 404)

        valid_url = reverse("votes:cast", args=["post", self.post.pk])
        self.assertEqual(self.client.post(valid_url, {"value": "0"}).status_code, 400)
        self.assertEqual(self.client.post(valid_url, {"value": "2"}).status_code, 400)
        self.assertEqual(self.client.post(valid_url, {"value": "abc"}).status_code, 400)
        self.assertEqual(self.client.post(valid_url, {}).status_code, 400)

        bad_type_url = reverse("votes:cast", args=["category", self.post.pk])
        self.assertEqual(self.client.post(bad_type_url, {"value": "1"}).status_code, 400)

    def test_external_next_url_is_ignored(self) -> None:
        self.client.force_login(self.student)
        url = reverse("votes:cast", args=["post", self.post.pk])
        resp = self.client.post(url, {"value": "1", "next": "https://evil.example/phish"})
        self.assertRedirects(
            resp,
            reverse("posts:post_detail", args=[self.post.pk]),
            fetch_redirect_response=False,
        )

    def test_controls_template_renders_contract_context(self) -> None:
        html = render_to_string(
            "votes/_controls.html",
            {
                "target_type": "post",
                "target_id": self.post.pk,
                "score": 5,
                "user_vote": 1,
            },
        )
        self.assertIn(reverse("votes:cast", args=["post", self.post.pk]), html)
        self.assertIn("icon-button--active", html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn(">5<", html)

    def test_preview_view_respects_debug_setting(self) -> None:
        preview_url = reverse("votes:preview")
        with override_settings(DEBUG=False):
            self.assertEqual(self.client.get(preview_url).status_code, 404)

        with override_settings(DEBUG=True):
            response = self.client.get(preview_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.post.title)
            self.assertContains(response, self.comment.body)
