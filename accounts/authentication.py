from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        # 1. 调用父类方法获取用户
        user = super().get_user(validated_token)
        
        # 2. 校验密码指纹
        token_pwd_fp = validated_token.get('pwd_fp')
        
        # 取密码哈希后8位
        if not token_pwd_fp or token_pwd_fp != user.password[-8:]:
            raise InvalidToken('密码已修改，请重新登录')

        token_role = validated_token.get('role')
        token_role_type = validated_token.get('role_type')
        
        if token_role and (token_role != user.role or token_role_type != user.role_type):
            raise InvalidToken('您的职位权限已发生变更，请重新登录')
            
        return user