from django.urls import path
from . import views

# 命名空间
app_name = 'accounts'

urlpatterns = [
    path('ping/', views.ping, name='ping')
]