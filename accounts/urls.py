from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # 发送注册-验证码
    path('send-code/', views.send_verification_code, name='send_code'),
    # 企业注册
    path('register/', views.register, name='register'),
    # 统一登录
    path('login/', views.login, name='login'),
    # 董事邀请员工、HR、法务
    path('invite/', views.invite, name='invite'),
    # 激活账号
    path('activate/', views.activate, name='activate'),
    # 角色委任
    path('assign-role/', views.assign_role, name='assign_role'),
]