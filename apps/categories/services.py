"""M2: logika kategorii, kontrola uprawnień administratora i kontrakt CategoryCatalog."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, QuerySet
from django.utils.text import slugify

from apps.accounts.services import DjangoAccessChecker
from core.contracts import AccessChecker

from .models import Category

NAME_MAX_LENGTH = 100
SLUG_MAX_LENGTH = 120
# Slug używany przez tablicę (core/views.py) jako "wszystkie posty"; kategoria nie może go zajmować.
RESERVED_SLUG = "wszystkie"


class CategoryInUseError(Exception):
    """Kategoria ma przypisane posty, więc baza blokuje jej usunięcie (PROTECT w Post.category)."""


class DjangoCategoryCatalog:
    """Implementacja kontraktu CategoryCatalog z core/contracts.py.

    Dzięki niej M3 sprawdza istnienie kategorii przez kontrakt, bez zależności od modelu Category.
    """

    def category_exists(self, category_id: int) -> bool:
        return Category.objects.filter(pk=category_id).exists()


# Parametr access_checker jest wstrzykiwany (DIP): w produkcji domyślnie DjangoAccessChecker z M1,
# a w testach jednostkowych można podstawić atrapę bez tabel użytkowników.
def is_admin(actor_id: int | None, access_checker: AccessChecker | None = None) -> bool:
    checker = access_checker or DjangoAccessChecker()
    return checker.has_role(actor_id, "admin")


def ensure_admin(actor_id: int | None, access_checker: AccessChecker | None = None) -> None:
    """Jedyne miejsce kontroli uprawnień M2. Rolę ustala AccessChecker, a nie is_staff."""
    if not is_admin(actor_id, access_checker):
        raise PermissionDenied("Operacja wymaga roli administratora.")


def build_category_slug(name: str, raw_slug: str = "") -> str:
    # slugify() zamienia ą, ć, ę, ń, ó, ś, ź, ż na litery łacińskie, ale pomija ł i Ł,
    # dlatego tylko te dwie litery są zamieniane ręcznie.
    source = (raw_slug.strip() or name).replace("ł", "l").replace("Ł", "L")
    slug = slugify(source)
    if not slug:
        raise ValidationError({"slug": "Nie można utworzyć adresu z podanej nazwy. Użyj liter lub cyfr."})
    return slug


def validate_category_fields(
    name: str, raw_slug: str = "", exclude_id: int | None = None
) -> tuple[str, str]:
    """Jedno źródło reguł walidacji (używane przez formularz i serwisy). Zwraca (nazwa, slug)."""
    name = name.strip()
    if not name:
        raise ValidationError({"name": "Podaj nazwę kategorii."})
    if len(name) > NAME_MAX_LENGTH:
        raise ValidationError({"name": f"Nazwa może mieć najwyżej {NAME_MAX_LENGTH} znaków."})

    slug = build_category_slug(name, raw_slug)
    if len(slug) > SLUG_MAX_LENGTH:
        raise ValidationError({"slug": f"Adres może mieć najwyżej {SLUG_MAX_LENGTH} znaków."})
    if slug == RESERVED_SLUG:
        raise ValidationError({"slug": "Ten adres jest zarezerwowany. Wybierz inny."})

    # PostgreSQL traktuje unique=True z uwzględnieniem wielkości liter, stąd osobne sprawdzenie iexact.
    others = Category.objects.all()
    if exclude_id is not None:
        others = others.exclude(pk=exclude_id)
    errors: dict[str, str] = {}
    if others.filter(name__iexact=name).exists():
        errors["name"] = "Kategoria o takiej nazwie już istnieje."
    if others.filter(slug__iexact=slug).exists():
        errors["slug"] = "Kategoria o takim adresie już istnieje."
    if errors:
        raise ValidationError(errors)
    return name, slug


def list_categories() -> QuerySet[Category]:
    return Category.objects.all()


def _save(category: Category) -> None:
    # Savepoint chroni transakcję wywołującego, a IntegrityError obsługuje równoczesny zapis tej samej nazwy.
    try:
        with transaction.atomic():
            category.save()
    except IntegrityError as exc:
        raise ValidationError("Kategoria o takiej nazwie lub adresie już istnieje.") from exc


def create_category(
    actor_id: int | None,
    name: str,
    slug: str = "",
    description: str = "",
    access_checker: AccessChecker | None = None,
) -> Category:
    ensure_admin(actor_id, access_checker)
    clean_name, clean_slug = validate_category_fields(name, slug)
    category = Category(name=clean_name, slug=clean_slug, description=description.strip())
    _save(category)
    return category


def update_category(
    actor_id: int | None,
    category: Category,
    name: str,
    slug: str = "",
    description: str = "",
    access_checker: AccessChecker | None = None,
) -> Category:
    ensure_admin(actor_id, access_checker)
    category.name, category.slug = validate_category_fields(name, slug, exclude_id=category.pk)
    category.description = description.strip()
    _save(category)
    return category


def delete_category(
    actor_id: int | None, category: Category, access_checker: AccessChecker | None = None
) -> None:
    ensure_admin(actor_id, access_checker)
    try:
        category.delete()
    except ProtectedError as exc:
        # apps.posts nie jest importowane: ProtectedError pochodzi z Django, więc M2 nie zależy od M3.
        raise CategoryInUseError(category.name) from exc
