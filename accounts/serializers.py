# DRF
from rest_framework import serializers
# 当前app自定义代码
from .models import Tenant, User
class RegisterSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=100, label='公司名称')
    full_name = serializers.CharField(max_length=50, label='姓名')
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(min_length=6, write_only=True, label='密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')
    code = serializers.CharField(max_length=6, label='验证码')

    def valiadte_email(self, value):
        if User.objects.filter(email=value).exists():