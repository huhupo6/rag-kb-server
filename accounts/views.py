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
from .models import User, BannedEmail
from .utils import redis_client
from .serializers import (
    SendCodeSerializer,
    RegisterSerializer,
    LoginSerializer,
    InviteSerializer,
    ActivateSerializer,
    AssignRoleSerializer,
    ResignUserSerializer,
    SelfResignSerializer,
    CancelTenantSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)

# 企业注册-发送验证码 接口
@api_view(['POST'])
def send_verification_code(request):
    serializer = SendCodeSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收邮箱'})
    return Response(serializer.errors, status=400)

# 企业注册 接口
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

# 统一登录 接口
@api_view(['POST'])
def login(request):
    serializer = LoginSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.validated_data['user']

        refresh = RefreshToken.for_user(user)
        refresh['pwd_fp'] = user.password[-8:]
        refresh.access_token['pwd_fp'] = user.password[-8:]
        refresh['role'] = user.role
        refresh.access_token['role'] = user.role
        refresh['role_type'] = user.role_type
        refresh.access_token['role_type'] = user.role_type

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

# 董事邀请员工、HR、法务 / HR邀请员工 接口
@api_view(['POST'])
def invite(request):
    request_user = User.objects.select_related("tenant").get(pk=request.user.pk)
    
    request.user = request_user

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

# 激活账号 接口
@api_view(['POST'])
def activate(request):
    serializer = ActivateSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '激活成功，请前往登录'}, status=200)
    return Response(serializer.errors, status=400)

# 角色委任 接口
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

# 员工离职 接口
@api_view(['POST'])
def resign_user(request):
    serializer = ResignUserSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        target = serializer.save()
        return Response({
            'message': '离职处理成功，邮箱已释放',
            'user': {
                'id': target.id,
                'full_name': target.full_name,
                'status': target.status,
            }
        }, status=200)
    return Response(serializer.errors, status=400)

# 董事离职 接口
@api_view(['POST'])
def self_resign(request):
    serializer = SelfResignSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        user = serializer.save()
        return Response({
            'message': '离职成功，感谢您的付出',
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'status': user.status,
            }
        }, status=200)
    return Response(serializer.errors, status=400)

# 董事注销公司 接口
@api_view(['POST'])
def cancel_tenant(request):
    serializer = CancelTenantSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        tenant = serializer.save()
        return Response({
            'message': '公司已注销，感谢您的使用',
            'tenant': {
                'id': tenant.id,
                'name': tenant.name,
                'status': tenant.status,
            }
        }, status=200)
    return Response(serializer.errors, status=400)

# 用户忘记密码-发送验证码 接口
@api_view(['POST'])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收'})
    return Response(serializer.errors, status=400)

# 用户忘记密码-重置密码 接口
@api_view(['POST'])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '密码重置成功，请用新密码登录'}, status=200)
    return Response(serializer.errors, status=400)
