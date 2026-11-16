"""
accounts/utils.py
账号模块通用工具函数
包含：邀请用户过期处理、角色中文映射、JWT令牌生成
"""

# Django 内置
from django.utils import timezone

# 第三方库
from rest_framework_simplejwt.tokens import RefreshToken


def expire_pending_user(user):
    """
    过期待激活的邀请用户
    功能：邀请链接过期时，对用户做失效处理
    逻辑：
        1. 给原邮箱后缀加上时间戳，释放邮箱，避免新用户无法使用该邮箱注册
        2. 修改用户状态为 expired（已过期）
        3. 清空激活令牌、令牌过期时间
        4. 只更新指定字段，减少数据库写入开销
    """
    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    user.email = f'{user.email}_expired_{timestamp}'
    user.status = 'expired'
    user.activation_token = ''
    user.activation_token_expires_at = None
    user.save(update_fields=[
        'email', 'status', 'activation_token', 'activation_token_expires_at'
    ])


def get_role_full_name(role, role_type):
    """
    角色名称翻译工具
    作用：将后端枚举的英文角色标识，转为前端展示用的中文角色名
    """
    if role == 'owner':
        return '董事'
    elif role == 'admin':
        if role_type == 'hr':
            return '管理员(HR)'
        elif role_type == 'legal':
            return '管理员(法务)'
        return '管理员'
    elif role == 'member':
        return '员工'
    return role


def generate_jwt_tokens(user):
    """
    生成JWT令牌（Access + Refresh）
    逻辑：
        1. 基于simplejwt生成标准refresh token
        2. 提取密码后8位指纹、角色、角色类型，注入到token的payload
        3. AccessToken 和 RefreshToken 都携带权限信息
        返回：(access_token, refresh_token)
    备注：pwd_fp用于后续密码变更后，直接使旧token失效
    """
    refresh = RefreshToken.for_user(user)

    # 提取密码指纹、角色信息
    pwd_fp = user.password[-8:] if user.password else ''
    role = user.role
    role_type = user.role_type

    # 往Refresh Token载荷注入字段
    refresh['pwd_fp'] = pwd_fp
    refresh['role'] = role
    refresh['role_type'] = role_type

    # 往Access Token载荷注入字段
    refresh.access_token['pwd_fp'] = pwd_fp
    refresh.access_token['role'] = role
    refresh.access_token['role_type'] = role_type

    return str(refresh.access_token), str(refresh)
