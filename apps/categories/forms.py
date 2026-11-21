"""M2: formularz kategorii. Reguły walidacji pochodzą z services.py."""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from .models import Category
from .services import SLUG_MAX_LENGTH, validate_category_fields


class CategoryForm(forms.ModelForm):
    """Formularz dodawania i edycji kategorii.

    Pole slug jest nadpisane jako CharField: wbudowany walidator SlugField odrzucałby wartości
    ze spacjami lub polskimi znakami, zanim zostałyby znormalizowane. Cała walidacja przechodzi
    przez validate_category_fields(), więc formularz i serwisy stosują te same reguły.
    """

    slug = forms.CharField(
        label="Adres",
        max_length=SLUG_MAX_LENGTH,
        required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    class Meta:
        model = Category
        fields = ("name", "slug", "description")
        labels = {"name": "Nazwa", "description": "Opis"}
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "description": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
        }

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        # Pola, które nie przeszły walidacji typu, są pomijane, żeby nie dublować komunikatów.
        if "name" not in cleaned or "slug" not in cleaned:
            return cleaned
        try:
            cleaned["name"], cleaned["slug"] = validate_category_fields(
                cleaned["name"], cleaned["slug"], exclude_id=self.instance.pk
            )
        except ValidationError as error:
            self.add_error(None, error)
        return cleaned
