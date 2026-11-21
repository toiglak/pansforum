"""M2: widoki kategorii. Widok przyjmuje żądanie i woła serwis, bez własnej logiki biznesowej."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .forms import CategoryForm
from .models import Category
from .services import (
    CategoryInUseError,
    create_category,
    delete_category,
    ensure_admin,
    is_admin,
    list_categories,
    update_category,
)


@require_GET
def category_list(request: HttpRequest) -> HttpResponse:
    """Publiczna lista kategorii (GET /categories/); przyciski zarządzania widzi tylko administrator."""
    # Flaga is_admin służy wyłącznie do wyświetlenia przycisków; uprawnienia egzekwuje serwis.
    return render(request, "categories/category_list.html", {
        "categories": list_categories(),
        "is_admin": is_admin(request.user.id),
    })


@login_required
@require_http_methods(["GET", "POST"])
def category_create(request: HttpRequest) -> HttpResponse:
    """Dodawanie kategorii przez administratora; rola jest sprawdzana już na GET, przed pokazaniem formularza."""
    ensure_admin(request.user.id)
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_category(
                request.user.id,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                description=form.cleaned_data["description"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Dodano kategorię.")
            return redirect("categories:list")
    return render(request, "categories/category_form.html", {
        "form": form, "heading": "Nowa kategoria", "submit_label": "Dodaj kategorię",
    })


@login_required
@require_http_methods(["GET", "POST"])
def category_edit(request: HttpRequest, category_id: int) -> HttpResponse:
    """Edycja kategorii po id; rola jest sprawdzana przed pobraniem rekordu, więc nie-admin dostaje 403, nie 404."""
    ensure_admin(request.user.id)
    category = get_object_or_404(Category, pk=category_id)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        try:
            update_category(
                request.user.id,
                category,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                description=form.cleaned_data["description"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Zapisano zmiany.")
            return redirect("categories:list")
    return render(request, "categories/category_form.html", {
        "form": form, "heading": "Edytuj kategorię", "submit_label": "Zapisz zmiany",
    })


@login_required
@require_http_methods(["GET", "POST"])
def category_delete(request: HttpRequest, category_id: int) -> HttpResponse:
    """Usuwanie kategorii: GET pokazuje potwierdzenie, a dane zmienia dopiero POST z tokenem CSRF."""
    ensure_admin(request.user.id)
    category = get_object_or_404(Category, pk=category_id)
    if request.method == "POST":
        try:
            delete_category(request.user.id, category)
        except CategoryInUseError:
            messages.error(request, "Nie można usunąć kategorii, która ma posty. Przenieś lub usuń je najpierw.")
        else:
            messages.success(request, "Usunięto kategorię.")
        return redirect("categories:list")
    return render(request, "categories/category_confirm_delete.html", {"category": category})
