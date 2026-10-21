# 1. Python内置
import random

# 2. Django
from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings

# 3. DRF
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

# 4. 当前app自定义代码
from .models import User, BannedEmail
from .utils import redis_client, check_tenant_active
from .serializers import (
    SendCodeSerializer,
    RegisterSerializer,
    UserListSerializer,
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
@permission_classes([AllowAny])
def send_verification_code(request):
    serializer = SendCodeSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收邮箱'})
    return Response(serializer.errors, status=400)

# 企业注册 接口
@api_view(['POST'])
@permission_classes([AllowAny])
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
@permission_classes([AllowAny])
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

# 人员列表展示 API
@api_view(['GET'])
def list_users(request):
    # 1. 校验租户状态
    check_tenant_active(request.user)

    # 2. 权限校验：只有董事和 HR 才能访问人员管理列表
    is_owner = request.user.role == 'owner'
    is_hr = request.user.role == 'admin' and request.user.role_type == 'hr'
    if not (is_owner or is_hr):
        return Response({'error': '您没有权限查看人员列表'}, status=403)

    # 3. 基础查询
    queryset = User.objects.filter(tenant=request.user.tenant)
    
    # 4. 支持前端模糊搜索（按姓名或邮箱）
    search_keyword = request.query_params.get('search', '').strip()
    if search_keyword:
        queryset = queryset.filter(
            Q(full_name__icontains=search_keyword) | Q(email__icontains=search_keyword)
        )
        
    # 5. 按邀请时间倒序
    queryset = queryset.order_by('-invited_at')

    # 6. 分页处理（每页 20 条）
    paginator = PageNumberPagination()
    paginator.page_size = 20
    paginated_users = paginator.paginate_queryset(queryset, request)

    serializer = UserListSerializer(paginated_users, many=True, context={'request': request})
    
    return paginator.get_paginated_response(serializer.data)

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
@permission_classes([AllowAny])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收'})
    return Response(serializer.errors, status=400)

# 用户忘记密码-重置密码 接口
@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '密码重置成功，请用新密码登录'}, status=200)
    return Response(serializer.errors, status=400)
