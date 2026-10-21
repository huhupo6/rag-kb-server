from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # 发送注册-验证码
    path('send-code/', views.send_verification_code, name='send_code'),
    # 企业注册
    path('register/', views.register, name='register'),
    # 人员列表
    path('users/', views.list_users, name='list_users'),
    # 统一登录
    path('login/', views.login, name='login'),
    # 董事HR邀请成员
    path('invite/', views.invite, name='invite'),
    # 激活账号
    path('activate/', views.activate, name='activate'),
    # 角色委任
    path('assign-role/', views.assign_role, name='assign_role'),
    # 员工离职
    path('resign/', views.resign_user, name='resign'),
    # 董事离职
    path('me/resign/', views.self_resign, name='self_resign'),
    # 董事注销公司
    path('tenant/cancel/', views.cancel_tenant, name='cancel_tenant'),
    # 忘记密码 发送验证码
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    # 忘记密码 重置验证码
    path('reset-password/', views.reset_password, name='reset_password'),
]