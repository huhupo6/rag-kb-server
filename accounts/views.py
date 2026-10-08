# 1. Python内置
import random

# 2. Django
from django.core.mail import send_mail
from django.conf import settings

# 3. DRF
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

# 4. 当前app自定义代码
from .models import User, BannedEmail, Tenant, AuditLog
from .utils import redis_client
from .serializers import RegisterSerializer, LoginSerializer, InviteSerializer, ActivateSerializer, AssignRoleSerializer
# 发送验证码视图
@api_view(['POST'])
def send_verification_code(request):
    email = request.data.get('email', '').strip()

    # 1. 校验邮箱是否填写
    if not email:
        return Response({'error': '请输入邮箱'}, status=400)

    # 2. 检查邮箱是否被平台封禁
    if BannedEmail.objects.filter(email=email).exists():
        return Response({'error': '该邮箱已被平台封禁，请联系客服'}, status=400)

    # 3. 检查邮箱是否已被注册（董事注册时，一个邮箱只能注册一次）
    if User.objects.filter(email=email).exists():
        return Response({'error': '该邮箱已被注册'}, status=400)

    # 4. 同一邮箱 60 秒内只能发一次
    if redis_client.exists(f'reg_code_limit:{email}'):
        return Response({'error': '验证码已发送，请 60 秒后再试'}, status=429)

    # 5. 生成 6 位数字验证码
    code = ''.join(random.choices('0123456789', k=6))
    
    # 6. 存入 Redis，5 分钟过期，60 秒不能重复发送
    redis_client.setex(f'reg_code:{email}', 300, code)
    redis_client.setex(f'reg_code_limit:{email}', 60, '1')

    # 7. 发送邮件
    try:
        send_mail(
            subject='【企业知识库】注册验证码',
            message=f'您的验证码是：{code}，5 分钟内有效，请勿泄露给他人。',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as e:
        return Response({'error': f'邮件发送失败：{str(e)}'}, status=500)

    return Response({'message': '验证码已发送，请查收邮箱'})

# 企业注册视图
@api_view(['POST'])
def register(request):
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.save()

        # 注册后，JWT签发
        refresh = RefreshToken.for_user(user)

        return Response({
            'message': '注册成功',
            'user': {
                'id': user.id,
                'email': user.email,
                'full_name': user.full_name,
                'role': user.role,
                'tenant_name': user.tenant.name,
            },
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }, status=201)

    # 校验失败，返回错误信息
    return Response(serializer.errors, status=400)

# 统一登录接口视图
@api_view(['POST'])
def login(request):
    serializer = LoginSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.validated_data['user']
        refresh = RefreshToken.for_user(user)

        return Response({
            'message': '登录成功',
            'user': {
                'id': user.id,
                'email': user.email,
                'full_name': user.full_name,
                'role': user.role,
                'role_type': user.role_type,
                'tenant_id': user.tenant_id,
                'tenant_name': user.tenant.name if user.tenant else '',
            },
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }, status=200)

    # 校验失败，返回错误信息
    return Response(serializer.errors, status=400)

# 董事邀请员工、HR、法务 / HR邀请员工 视图
@api_view(['POST'])
def invite(request):
    serializer = InviteSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        user = serializer.save()
        return Response({
            'message': '邀请已发送，请等待对方激活',
            'user': {
                'id': user.id,
                'email': user.email,
                'full_name': user.full_name,
                'role': user.role,
                'role_type': user.role_type,
            }
        }, status=201)
    return Response(serializer.errors, status=400)

# 激活账号 视图
@api_view(['POST'])
def activate(request):
    serializer = ActivateSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '激活成功，请前往登录'}, status=200)
    return Response(serializer.errors, status=400)

# 角色委任
@api_view(['POST'])
def assign_role(request):
    serializer = AssignRoleSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        target = serializer.save()
        return Response({
            'message': '委任成功',
            'user': {
                'id': target.id,
                'email': target.email,
                'full_name': target.full_name,
                'role': target.role,
                'role_type': target.role_type,
            }
        }, status=200)
    return Response(serializer.errors, status=400)