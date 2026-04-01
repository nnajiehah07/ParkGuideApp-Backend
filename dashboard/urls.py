from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index_redirect, name='index'),
    path('login/', LoginView.as_view(template_name='dashboard/login.html', next_page='dashboard:home'), name='login'),
    path('logout/', LogoutView.as_view(next_page='dashboard:login'), name='logout'),
    path('dashboard/', views.dashboard_home, name='home'),
    path('dashboard/users/', views.dashboard_users, name='users'),
    path('dashboard/courses/', views.dashboard_courses, name='courses'),
    path('dashboard/progress/', views.dashboard_progress, name='progress'),
    path('dashboard/badges/', views.dashboard_badges, name='badges'),
    path('dashboard/notifications/', views.dashboard_notifications, name='notifications'),
]
