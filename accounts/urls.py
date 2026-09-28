from django.urls import path
from . import views

# 命名 accounts
app_name = 'accounts'

urlpatterns = [
    # 发送注册-验证码
    path('send-code/', views.send_verification_code, name='send_code'),
]