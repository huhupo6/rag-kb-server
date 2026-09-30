from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # 发送注册-验证码
    path('send-code/', views.send_verification_code, name='send_code'),
    # 企业注册
    path('register/', views.register, name='register'),
]