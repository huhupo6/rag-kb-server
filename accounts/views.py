
# 1. Django
from django.db.models import Q

# 2. DRF
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

# 3. 当前app自定义代码
from .models import User
from common.permissions import check_tenant_active
from .utils import generate_jwt_tokens
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


@api_view(['POST'])
@permission_classes([AllowAny])
def send_verification_code(request):
    """企业注册：发送邮箱验证码接口，无需登录"""
    serializer = SendCodeSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收邮箱'})
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    """企业账号注册接口，注册成功自动生成JWT令牌"""
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.save()
        # 调用工具函数生成 access_token、refresh_token
        access_token, refresh_token = generate_jwt_tokens(user)

        return Response({
            'message': '注册成功',
            'user': {
                'id': user.id,
                'email': user.email,
                'full_name': user.full_name,
                'role': user.role,
                'tenant_name': user.tenant.name,
            },
            'access': access_token,
            'refresh': refresh_token,
        }, status=201)

    # 参数校验失败返回错误信息
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """账号登录接口，校验通过返回JWT令牌"""
    serializer = LoginSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.validated_data['user']
        # 调用工具函数生成 access_token、refresh_token
        access_token, refresh_token = generate_jwt_tokens(user)

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
            'access': access_token,
            'refresh': refresh_token,
        }, status=200)

    # 账号密码校验失败返回错误信息
    return Response(serializer.errors, status=400)


@api_view(['GET'])
def list_users(request):
    """
    租户人员列表接口
    权限：仅董事(owner)、HR管理员可访问
    支持：姓名/邮箱模糊搜索、分页，按邀请时间倒序
    """
    # 校验租户是否处于可用状态
    check_tenant_active(request.user)

    # 权限判断：董事 或者 HR管理员
    is_owner = request.user.role == 'owner'
    is_hr = request.user.role == 'admin' and request.user.role_type == 'hr'
    if not (is_owner or is_hr):
        return Response({'error': '您没有权限查看人员列表'}, status=403)

    # 只查询当前租户下的用户
    queryset = User.objects.filter(tenant=request.user.tenant)

    # 前端传搜索关键词，模糊匹配姓名、邮箱
    search_keyword = request.query_params.get('search', '').strip()
    if search_keyword:
        queryset = queryset.filter(
            Q(full_name__icontains=search_keyword) | Q(email__icontains=search_keyword)
        )

    # 按邀请时间倒序展示
    queryset = queryset.order_by('-invited_at')

    # 分页配置，每页20条
    paginator = PageNumberPagination()
    paginator.page_size = 20
    paginated_users = paginator.paginate_queryset(queryset, request)

    serializer = UserListSerializer(paginated_users, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)


@api_view(['POST'])
def invite(request):
    """董事/HR邀请新成员接口，生成待激活用户并发送邀请邮件"""
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


@api_view(['POST'])
@permission_classes([AllowAny])
def activate(request):
    """邀请链接激活账号接口，设置密码，无需登录"""
    serializer = ActivateSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '激活成功，请前往登录'}, status=200)
    return Response(serializer.errors, status=400)


@api_view(['POST'])
def assign_role(request):
    """角色委任接口，修改租户内用户角色，异步发送角色变更通知邮件"""
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


@api_view(['POST'])
def resign_user(request):
    """HR/董事操作员工离职接口，变更用户状态、释放邮箱"""
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


@api_view(['POST'])
def self_resign(request):
    """董事自主离职接口"""
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


@api_view(['POST'])
def cancel_tenant(request):
    """董事注销整个租户（公司）接口，注销后发送通知邮件给负责人"""
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


@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_password(request):
    """忘记密码：发送重置验证码接口，无需登录"""
    serializer = ForgotPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '验证码已发送，请查收'})
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request):
    """忘记密码：验证码校验+重置密码接口，无需登录"""
    serializer = ResetPasswordSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': '密码重置成功，请用新密码登录'}, status=200)
    return Response(serializer.errors, status=400)
