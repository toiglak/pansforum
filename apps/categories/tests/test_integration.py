"""Testy integracyjne M2: klient HTTP, prawdziwa baza i prawdziwy DjangoAccessChecker (bez atrap)."""

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Profile
from apps.categories.models import Category
from apps.categories.services import DjangoCategoryCatalog
from apps.posts.models import Post

User = get_user_model()
PASSWORD = "haslo-testowe-123"


class CategoryViewsTestCase(TestCase):
    """Baza testów integracyjnych: pełny stos HTTP, PostgreSQL i prawdziwy DjangoAccessChecker, bez atrap."""

    @classmethod
    def setUpTestData(cls) -> None:
        def make_user(username: str, role: str, **extra) -> User:
            user = User.objects.create_user(username=username, password=PASSWORD, **extra)
            Profile.objects.create(user=user, role=role)
            return user

        cls.admin = make_user("admin1", Profile.Role.ADMIN)
        cls.moderator = make_user("mod1", Profile.Role.MODERATOR)
        cls.student = make_user("student1", Profile.Role.USER)
        cls.staff_student = make_user("staff1", Profile.Role.USER, is_staff=True)
        cls.superuser = User.objects.create_superuser("root", password=PASSWORD)
        cls.category = Category.objects.create(name="Przedmioty", slug="przedmioty", description="Zajęcia")

    def messages_of(self, response) -> list[str]:
        return [str(m) for m in get_messages(response.wsgi_request)]


class CategoryCatalogIntegrationTests(CategoryViewsTestCase):
    """Prawdziwy DjangoCategoryCatalog na bazie, bez atrap."""

    def test_existing_category_exists(self) -> None:
        self.assertTrue(DjangoCategoryCatalog().category_exists(self.category.pk))

    def test_missing_or_deleted_category_does_not_exist(self) -> None:
        category_id = self.category.pk
        self.category.delete()
        self.assertFalse(DjangoCategoryCatalog().category_exists(category_id))
        self.assertFalse(DjangoCategoryCatalog().category_exists(99999))


class CategoryListTests(CategoryViewsTestCase):
    def test_guest_sees_categories_without_admin_actions(self) -> None:
        response = self.client.get(reverse("categories:list"))
        self.assertContains(response, "Przedmioty")
        self.assertContains(response, 'href="/?category=przedmioty"')
        self.assertNotContains(response, "Dodaj kategorię")
        self.assertNotContains(response, "Edytuj")

    def test_admin_sees_management_actions(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(reverse("categories:list"))
        self.assertContains(response, reverse("categories:create"))
        self.assertContains(response, reverse("categories:edit", args=[self.category.pk]))
        self.assertContains(response, reverse("categories:delete", args=[self.category.pk]))

    def test_empty_state_is_shown_without_categories(self) -> None:
        Category.objects.all().delete()
        self.assertContains(self.client.get(reverse("categories:list")), "Brak kategorii")


class CategoryCreateTests(CategoryViewsTestCase):
    @property
    def url(self) -> str:
        return reverse("categories:create")

    def test_admin_and_superuser_create_category(self) -> None:
        for user, name in ((self.admin, "Koła naukowe"), (self.superuser, "Sprawy dziekanatu")):
            self.client.force_login(user)
            response = self.client.post(self.url, {"name": name, "slug": "", "description": ""})
            self.assertRedirects(response, reverse("categories:list"))
        self.assertTrue(Category.objects.filter(slug="kola-naukowe").exists())
        self.assertTrue(Category.objects.filter(slug="sprawy-dziekanatu").exists())

    def test_non_admins_get_403_on_get_and_post(self) -> None:
        for user in (self.student, self.moderator, self.staff_student):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url, {"name": "X"}).status_code, 403)
        self.assertFalse(Category.objects.filter(name="X").exists())

    def test_guest_is_redirected_to_login(self) -> None:
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={self.url}")

    def test_invalid_data_returns_form_errors(self) -> None:
        self.client.force_login(self.admin)
        cases = {
            "pusta nazwa": {"name": "  ", "slug": ""},
            "duplikat bez względu na wielkość liter": {"name": "przedmioty", "slug": "inny"},
            "zarezerwowany slug": {"name": "Cokolwiek", "slug": "wszystkie"},
            "slug bez znaków": {"name": "!!!", "slug": ""},
        }
        for label, data in cases.items():
            with self.subTest(label):
                response = self.client.post(self.url, {**data, "description": ""})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertEqual(Category.objects.count(), 1)


class CategoryEditTests(CategoryViewsTestCase):
    def url(self, category_id: int | None = None) -> str:
        return reverse("categories:edit", args=[category_id or self.category.pk])

    def test_admin_updates_category(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {"name": "Zajęcia", "slug": "zajecia", "description": "Nowy opis"})
        self.assertRedirects(response, reverse("categories:list"))
        self.category.refresh_from_db()
        self.assertEqual((self.category.name, self.category.slug, self.category.description), ("Zajęcia", "zajecia", "Nowy opis"))

    def test_edit_keeps_own_name_without_duplicate_error(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {"name": "Przedmioty", "slug": "przedmioty", "description": "Zmieniony"})
        self.assertRedirects(response, reverse("categories:list"))

    def test_non_admin_gets_403_and_data_is_unchanged(self) -> None:
        for user in (self.student, self.moderator, self.staff_student):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)
                self.assertEqual(self.client.post(self.url(), {"name": "Hack", "slug": "hack"}).status_code, 403)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "Przedmioty")

    def test_missing_category_returns_404(self) -> None:
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url(99999)).status_code, 404)


class CategoryDeleteTests(CategoryViewsTestCase):
    def url(self, category_id: int | None = None) -> str:
        return reverse("categories:delete", args=[category_id or self.category.pk])

    def test_get_shows_confirmation_and_does_not_delete(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(self.url())
        self.assertContains(response, "Przedmioty")
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_admin_deletes_empty_category_with_post(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(self.url())
        self.assertRedirects(response, reverse("categories:list"))
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())

    def test_category_with_post_is_kept_and_error_message_is_shown(self) -> None:
        Post.objects.create(author=self.student, category=self.category, title="Pytanie", body="Treść")
        self.client.force_login(self.admin)
        response = self.client.post(self.url())
        self.assertRedirects(response, reverse("categories:list"))
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(any("ma posty" in m for m in self.messages_of(response)))

    def test_non_admin_gets_403_and_missing_category_404(self) -> None:
        self.client.force_login(self.moderator)
        self.assertEqual(self.client.post(self.url()).status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(99999)).status_code, 404)
