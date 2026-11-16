"""
accounts/authentication.py
自定义JWT认证类，继承simplejwt原生认证
额外校验：密码指纹、角色权限是否和当前用户一致，变更后强制重新登录
"""

# 第三方库
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        """
        重写获取用户逻辑，在原生JWT校验基础上增加业务校验
        校验项：
            1. 密码指纹：token里保存的密码后8位 和 当前用户密码后8位对比，密码修改则拒绝token
            2. 角色信息：token携带的role、role_type 和 当前用户角色对比，权限变更则拒绝token
        """
        # 调用父类原生逻辑，根据token拿到用户对象
        user = super().get_user(validated_token)

        # 取出token载荷中的密码指纹
        token_pwd_fp = validated_token.get('pwd_fp')

        # 对比密码指纹，不一致代表密码已经修改，令牌失效
        if not token_pwd_fp or token_pwd_fp != user.password[-8:]:
            raise InvalidToken('密码已修改，请重新登录')

        # 取出token载荷中的角色、角色类型
        token_role = validated_token.get('role')
        token_role_type = validated_token.get('role_type')

        # 角色发生变更，令牌失效，强制重新登录
        if token_role and (token_role != user.role or token_role_type != user.role_type):
            raise InvalidToken('您的职位权限已发生变更，请重新登录')

        return user
