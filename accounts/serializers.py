# 1. Python内置
import re
# 2. Django
from django.contrib.auth.hashers import make_password
from django.db import transaction
# 3. DRF
from rest_framework import serializers
# 4. 当前app自定义代码
from .models import Tenant, User
from .utils import redis_client

class RegisterSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=100, label='公司名称')
    full_name = serializers.CharField(max_length=50, label='姓名')
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(min_length=6, write_only=True, label='密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')
    code = serializers.CharField(max_length=6, label='验证码')

    def valiadte_email(self, value):
        # 检验邮箱是否未被注册
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def validate(self, attrs):
        # 检验二次密码是否一致
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')

        # 校验验证码
        email = attrs['email']
        code = attrs['code']
        saved_code = redis_client.get(f'reg_code:{email}')

        if not saved_code or saved_code != code:
                raise serializers.ValidationError('验证码错误或已过期')

        return attrs

    def create(self, validated_data):
        # 开启事务，保证原子性
        with transaction.atomic():
            
            # 1. 创建租户（公司）
            tenant = Tenant.objects.create(name=validated_data['company_name'])

            # 2. 创建董事账号
            user = User.objects.create(
                tenant=tenant,
                email=validated_data['email'],
                password=make_password(validated_data['password']),  # 密码加密
                full_name=validated_data['full_name'],
                role='owner',        # 角色：董事
                role_type='',        # 董事/员工为空
                is_active=True,      # 注册即激活，不用邮件激活码激活
            )

            # 4. 注册成功后，删掉 Redis 里的验证码
            redis_client.delete(f'reg_code:{validated_data["email"]}')

        return user