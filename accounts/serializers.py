# 1. Python内置
import secrets
from datetime import timedelta

# 2. Django
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

# 3. DRF
from rest_framework import serializers

# 4. 当前app自定义代码
from .models import Tenant, User, BannedEmail, AuditLog
from .utils import redis_client, check_tenant_active

# 企业注册 序列化器
class RegisterSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=100, label='公司名称')
    full_name = serializers.CharField(max_length=50, label='姓名')
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(min_length=6, write_only=True, label='密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')
    code = serializers.CharField(max_length=6, label='验证码')

    def validate_email(self, value):
        # 检查邮箱是否被平台封禁
        if BannedEmail.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError('该邮箱已被封禁，无法注册')

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
                original_email=validated_data['email'],
                password=make_password(validated_data['password']),  # 密码加密
                full_name=validated_data['full_name'],
                role='owner',        # 角色：董事
                role_type='',        # 董事/员工为空
                status='active',      # 注册即在职，不用邮件激活码激活
            )

            # 4. 注册成功后，删掉 Redis 里的验证码
            redis_client.delete(f'reg_code:{validated_data["email"]}')

        return user

# 统一登录 序列化器
class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(write_only=True, label='密码')

    def validate(self, attrs):
        email = attrs['email']
        password = attrs['password']

        # 1. 根据邮箱查用户
        user = User.objects.filter(email=email).first()
        if not user:
            raise serializers.ValidationError('邮箱或密码错误')

        # 2. 校验密码
        if not check_password(password, user.password):
            raise serializers.ValidationError('邮箱或密码错误')

        # 3. 校验账号是否被封禁
        if user.is_banned:
            raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')

        # 4. 校验账号状态
        if user.status != 'active':
            status_messages = {
                'pending': '账号未激活，请留意企业邀请邮件',
                'resigned': '账号已离职，请联系企业管理员',
                'expired': '邀请已过期，请联系企业管理员重新邀请',
            }
            raise serializers.ValidationError(
                status_messages.get(user.status, '账号状态异常，请联系企业管理员')
            )

        # 5. 校验租户状态
        check_tenant_active(user)

        attrs['user'] = user
        return attrs

# 董事邀请董事长、HR、法务、员工 / HR只能邀请员工 序列化器
class InviteSerializer(serializers.Serializer):
    email = serializers.EmailField(label='邮箱')
    full_name = serializers.CharField(max_length=50, label='姓名')
    role = serializers.ChoiceField(choices=['owner', 'admin', 'member'], label='系统角色')
    role_type = serializers.ChoiceField(choices=['hr', 'legal', ''], default='', required=False, label='管理员类型')

    def validate(self, attrs):
        request_user = self.context['request'].user

        # 统一租户校验
        check_tenant_active(request_user)

        # 1. 身份判断：董事长 / HR
        is_owner = request_user.role == 'owner'
        is_hr = request_user.role == 'admin' and request_user.role_type == 'hr'

        if not (is_owner or is_hr):
            raise serializers.ValidationError('您没有权限邀请用户')

        # 2. HR 只能邀请员工
        if is_hr and attrs['role'] != 'member':
            raise serializers.ValidationError('HR 只能邀请员工')

        # 3. 只有董事长能邀请另一位董事长
        if attrs['role'] == 'owner' and not is_owner:
            raise serializers.ValidationError('只有董事长可以邀请另一位董事长')
        
        # 4. 董事邀请 HR 或 法务 时，必须指定是 HR 或 法务
        if attrs['role'] == 'admin' and attrs['role_type'] not in ['hr', 'legal']:
            raise serializers.ValidationError('邀请管理员时必须指定 HR 或 法务')

        # 5. 董事邀请员工或董事长时，管理员类型必须为空
        if attrs['role'] in ['member', 'owner'] and attrs['role_type'] != '':
            raise serializers.ValidationError('邀请员工时 role_type 必须为空')

        # 6. 邮箱封禁检查
        if BannedEmail.objects.filter(email=attrs['email'], is_active=True).exists():
            raise serializers.ValidationError('该邮箱已被封禁，无法邀请')

        # 7. 邮箱不能重复
        if User.objects.filter(email=attrs['email']).exists():
            raise serializers.ValidationError('该邮箱已被注册')

        return attrs

    def create(self, validated_data):
        request_user = self.context['request'].user

        # 生成激活令牌，24 小时有效
        token = secrets.token_urlsafe(32)

        user = User.objects.create(
            tenant=request_user.tenant,
            email=validated_data['email'],
            full_name=validated_data['full_name'],
            password='',
            role=validated_data['role'],
            role_type=validated_data['role_type'],
            status='pending',
            activation_token=token,
            activation_token_expires_at=timezone.now() + timedelta(hours=24),
        )

        # 发送邀请邮件
        send_mail(
            subject='【知识库系统】您已被邀请加入',
            message=f'{request_user.full_name} 邀请您加入 {request_user.tenant.name}，'
                    f'请点击链接激活账号并设置密码：\n'
                    f'http://localhost:5173/activate?token={token}',  # 地址需要指向前端页面
                    from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )

        return user

# 激活账号 序列化器
class ActivateSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(min_length=6, write_only=True)
    confirm_password = serializers.CharField(min_length=6, write_only=True)

    def validate(self, attrs):
        # 校验两次密码是否一致
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')
        return attrs

    def create(self, validated_data):

        with transaction.atomic():
            # 加行锁，防止一个邀请码被两个人同时激活，导致数据异常
            user = User.objects.select_for_update().filter(
                activation_token=validated_data['token']
            ).first()

            # 判断激活码是否有效
            if not user:
                raise serializers.ValidationError('无效的激活链接')

            # 判断账号是否被封禁
            if user.is_banned:
                raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')

            # 判断所属公司是否被停用
            if user.tenant and user.tenant.status != 'active':
                raise serializers.ValidationError('所属公司已被平台停用')


            # 判断账号状态是否处于待激活状态
            if user.status != 'pending':
                raise serializers.ValidationError('该账号已激活或链接已失效')

            # 判断激活码是否过期
            if user.activation_token_expires_at < timezone.now():
                user.status = 'expired'
                user.save()
                raise serializers.ValidationError('激活链接已过期')

            # 数据库更新：设置密码、激活账号、清空激活码
            user.password = make_password(validated_data['password'])
            user.status = 'active'
            user.activated_at = timezone.now()
            user.activation_token = ''
            user.activation_token_expires_at = None
            user.save()

            return user

# 角色委任
class AssignRoleSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(label='目标用户ID')
    target_role = serializers.ChoiceField(
        choices=['admin', 'member'], label='目标角色'
    )
    target_role_type = serializers.ChoiceField(
        choices=['hr', 'legal', ''], default='', required=False, label='管理员类型'
    )

    def validate(self, attrs):
        # 拿到当前操作者
        operator = self.context['request'].user

        # 1. 只有董事长可以委任
        if operator.role != 'owner':
            raise serializers.ValidationError('只有董事长可以委任职位')

        # 2. 查询目标用户是否为本公司
        target = User.objects.filter(
            id=attrs['user_id'],
            tenant=operator.tenant,   # 天然隔离跨租户
        ).first()
        if not target:
            raise serializers.ValidationError('用户不存在或不属于本公司')
        
        # 3. 不能委任自己
        if target.id == operator.id:
            raise serializers.ValidationError('不能委任自己')

        # 4. 不能操作其他董事长
        if target.role == 'owner':
            raise serializers.ValidationError('董事长之间无法互相委任')

        # 5. 只能委任在职用户
        if target.status != 'active':
            raise serializers.ValidationError('只能委任在职用户')

        # 6. 目标角色与原角色相同，无需操作
        if (target.role == attrs['target_role']
                and target.role_type == attrs['target_role_type']):
            raise serializers.ValidationError('该用户已是此角色')

        # 7. 角色和 role_type 搭配校验
        # 委任HR/法务时，必须指定 role_type；
        if attrs['target_role'] == 'admin' and attrs['target_role_type'] not in ['hr', 'legal']:
            raise serializers.ValidationError('委任为管理员时必须指定 HR 或法务')
        # 委任员工时，role_type 必须为空
        if attrs['target_role'] == 'member' and attrs['target_role_type'] != '':
            raise serializers.ValidationError('委任为员工时 role_type 必须为空')

        attrs['target'] = target
        return attrs

    def save(self, **kwargs):
        target = self.validated_data['target']
        operator = self.context['request'].user
        old_role = target.role
        old_role_type = target.role_type

        with transaction.atomic():
            target.role = self.validated_data['target_role']
            target.role_type = self.validated_data['target_role_type']
            target.save(update_fields=['role', 'role_type'])

            AuditLog.objects.create(
                tenant=operator.tenant,
                operator=operator,
                target_user=target,
                action='assign_role',
                detail=f'角色从 {old_role}/{old_role_type or "无"} '
                       f'改为 {target.role}/{target.role_type or "无"}',
            )

        return target