"""Testy jednostkowe M2 z atrapą AccessChecker (bez DjangoAccessChecker i tabel M1)."""

from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase

from apps.categories.forms import CategoryForm
from apps.categories.models import Category
from apps.categories.services import (
    CategoryInUseError,
    build_category_slug,
    create_category,
    delete_category,
    ensure_admin,
    list_categories,
    update_category,
    validate_category_fields,
)
from core.contracts import ContentRef, PostRef, RoleName
from tests.fakes import FakeCategoryCatalog

ROLE_LEVEL = {"user": 0, "moderator": 1, "admin": 2}


class FakeAccessChecker:
    """Atrapa core.contracts.AccessChecker na słowniku user_id -> rola, z hierarchią user < moderator < admin."""

    def __init__(self, roles: dict[int, RoleName]) -> None:
        self._roles = roles

    def is_author(self, user_id: int | None, target: PostRef | ContentRef) -> bool:
        return user_id is not None and target.author_id == user_id

    def has_role(self, user_id: int | None, role: RoleName) -> bool:
        current = self._roles.get(user_id) if user_id is not None else None
        return current is not None and ROLE_LEVEL[current] >= ROLE_LEVEL[role]


CHECKER = FakeAccessChecker({1: "admin", 2: "moderator", 3: "user"})
ADMIN_ID, MODERATOR_ID, USER_ID = 1, 2, 3


class AccessDeniedTests(SimpleTestCase):
    """SimpleTestCase: odmowa następuje przed jakimkolwiek dostępem do bazy, więc baza jest zablokowana."""

    def test_non_admin_actors_are_denied(self) -> None:
        for actor_id in (None, USER_ID, MODERATOR_ID, 999):
            with self.subTest(actor_id=actor_id), self.assertRaises(PermissionDenied):
                ensure_admin(actor_id, CHECKER)

    def test_write_services_deny_non_admin_before_touching_database(self) -> None:
        category = Mock(spec=Category)
        for actor_id in (None, USER_ID, MODERATOR_ID):
            with self.subTest(actor_id=actor_id):
                with self.assertRaises(PermissionDenied):
                    create_category(actor_id, "Nowa", access_checker=CHECKER)
                with self.assertRaises(PermissionDenied):
                    update_category(actor_id, category, "Nowa", access_checker=CHECKER)
                with self.assertRaises(PermissionDenied):
                    delete_category(actor_id, category, access_checker=CHECKER)
        category.delete.assert_not_called()

    def test_admin_is_allowed(self) -> None:
        ensure_admin(ADMIN_ID, CHECKER)


class SlugTests(SimpleTestCase):
    def test_polish_l_with_stroke_is_transliterated(self) -> None:
        self.assertEqual(build_category_slug("Koła naukowe"), "kola-naukowe")
        self.assertEqual(build_category_slug("Ogłoszenia"), "ogloszenia")
        self.assertEqual(build_category_slug("ŁÓDŹ"), "lodz")

    def test_explicit_slug_is_used_and_normalized(self) -> None:
        self.assertEqual(build_category_slug("Dowolna", "Moj Slug"), "moj-slug")

    def test_slug_without_letters_or_digits_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            build_category_slug("!!!")


class DeleteInUseTests(SimpleTestCase):
    def test_protected_error_becomes_category_in_use_error(self) -> None:
        category = Mock(spec=Category)
        category.name = "Przedmioty"
        category.delete.side_effect = ProtectedError("chronione", set())
        with self.assertRaises(CategoryInUseError):
            delete_category(ADMIN_ID, category, CHECKER)


class FakeCategoryCatalogTests(SimpleTestCase):
    """Atrapa kontraktu CategoryCatalog (bez bazy); prawdziwa implementacja jest w test_integration.py."""

    def test_known_id_exists(self) -> None:
        self.assertTrue(FakeCategoryCatalog(existing_ids=[1, 4]).category_exists(4))

    def test_unknown_id_does_not_exist(self) -> None:
        self.assertFalse(FakeCategoryCatalog(existing_ids=[1]).category_exists(2))


class ListCategoriesTests(TestCase):
    def test_empty_when_no_categories(self) -> None:
        self.assertEqual(list(list_categories()), [])

    def test_sorted_alphabetically_by_name(self) -> None:
        for name in ("Sesja", "Akademik", "Projekty"):
            Category.objects.create(name=name, slug=name.lower())
        self.assertEqual([c.name for c in list_categories()], ["Akademik", "Projekty", "Sesja"])


class CategoryFormTests(TestCase):
    """Formularz przepuszcza ręczny slug do normalizacji zamiast odrzucać go walidatorem SlugField."""

    def test_manual_slug_with_spaces_and_polish_letters_is_normalized(self) -> None:
        form = CategoryForm({"name": "Koła", "slug": "Koła naukowe", "description": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "kola-naukowe")

    def test_duplicate_is_reported_once_on_the_field(self) -> None:
        Category.objects.create(name="Sesja", slug="sesja")
        form = CategoryForm({"name": "SESJA", "slug": "", "description": ""})
        self.assertFalse(form.is_valid())
        self.assertEqual(len(form.errors["name"]), 1)


class AdminWriteServicesTests(TestCase):
    """Ścieżki zapisu z atrapą AccessChecker (TestCase, bo zapisują do tabeli kategorii)."""

    def test_integrity_error_becomes_validation_error(self) -> None:
        with patch.object(Category, "save", side_effect=IntegrityError):
            with self.assertRaises(ValidationError):
                create_category(ADMIN_ID, "Wyścig", access_checker=CHECKER)

    def test_integrity_error_on_update_becomes_validation_error(self) -> None:
        category = create_category(ADMIN_ID, "Stara", access_checker=CHECKER)
        with patch.object(Category, "save", side_effect=IntegrityError):
            with self.assertRaises(ValidationError):
                update_category(ADMIN_ID, category, "Nowa", access_checker=CHECKER)

    def test_admin_creates_with_generated_slug_and_trimmed_fields(self) -> None:
        category = create_category(ADMIN_ID, "  Koła naukowe ", description=" Opis ", access_checker=CHECKER)
        self.assertEqual((category.name, category.slug, category.description), ("Koła naukowe", "kola-naukowe", "Opis"))
        self.assertEqual(list(list_categories()), [category])

    def test_duplicates_are_rejected_ignoring_case(self) -> None:
        create_category(ADMIN_ID, "Matematyka", access_checker=CHECKER)
        with self.assertRaises(ValidationError) as name_error:
            create_category(ADMIN_ID, "matematyka", slug="inny", access_checker=CHECKER)
        self.assertIn("name", name_error.exception.message_dict)
        with self.assertRaises(ValidationError) as slug_error:
            create_category(ADMIN_ID, "Inna nazwa", slug="MATEMATYKA", access_checker=CHECKER)
        self.assertIn("slug", slug_error.exception.message_dict)

    def test_reserved_and_empty_values_are_rejected(self) -> None:
        for name, slug in (("Wszystkie", ""), ("Cokolwiek", "wszystkie"), ("   ", ""), ("!!!", "")):
            with self.subTest(name=name, slug=slug), self.assertRaises(ValidationError):
                validate_category_fields(name, slug)

    def test_update_keeps_own_slug_and_rejects_foreign_one(self) -> None:
        first = create_category(ADMIN_ID, "Pierwsza", access_checker=CHECKER)
        create_category(ADMIN_ID, "Druga", access_checker=CHECKER)
        update_category(ADMIN_ID, first, "Pierwsza zmieniona", slug="pierwsza", access_checker=CHECKER)
        first.refresh_from_db()
        self.assertEqual((first.name, first.slug), ("Pierwsza zmieniona", "pierwsza"))
        with self.assertRaises(ValidationError):
            update_category(ADMIN_ID, first, "Pierwsza zmieniona", slug="druga", access_checker=CHECKER)

    def test_admin_deletes_empty_category(self) -> None:
        category = create_category(ADMIN_ID, "Do usunięcia", access_checker=CHECKER)
        delete_category(ADMIN_ID, category, CHECKER)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())
