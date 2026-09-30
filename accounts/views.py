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
from .models import User
from .utils import redis_client
from .serializers import RegisterSerializer, LoginSerializer

# 发送验证码接口视图
@api_view(['POST'])
def send_verification_code(request):
    email = request.data.get('email', '').strip()

    # 1. 校验邮箱是否填写
    if not email:
        return Response({'error': '请输入邮箱'}, status=400)

    # 2. 检查邮箱是否已被注册（董事注册时，一个邮箱只能注册一次）
    if User.objects.filter(email=email).exists():
        return Response({'error': '该邮箱已被注册'}, status=400)

    # 3. 频率限制：同一邮箱 60 秒内只能发一次
    if redis_client.exists(f'reg_code_limit:{email}'):
        return Response({'error': '验证码已发送，请 60 秒后再试'}, status=429)

    # 4. 生成 6 位数字验证码
    code = ''.join(random.choices('0123456789', k=6))
    
    # 5. 存入 Redis，5 分钟过期，60 秒不能重复发送
    redis_client.setex(f'reg_code:{email}', 300, code)
    redis_client.setex(f'reg_code_limit:{email}', 60, '1')

    # 6. 发送邮件
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

# 企业注册接口视图
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