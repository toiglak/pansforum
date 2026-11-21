from django.urls import path

from . import views

app_name = "categories"

# Edycja i usuwanie po id: zmiana sluga nie psuje linków, a "create" nie koliduje z parametrem.
urlpatterns = [
    path("", views.category_list, name="list"),
    path("create/", views.category_create, name="create"),
    path("<int:category_id>/edit/", views.category_edit, name="edit"),
    path("<int:category_id>/delete/", views.category_delete, name="delete"),
]
