from django.urls import path

from . import views

app_name = "votes"

urlpatterns = [
    path("preview/", views.preview_view, name="preview"),
    path("<str:target_type>/<int:target_id>/", views.cast_vote_view, name="cast"),
    path("<str:target_type>/<int:target_id>/remove/", views.remove_vote_view, name="remove"),
]
