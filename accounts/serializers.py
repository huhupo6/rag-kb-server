"""
accounts/serializers.py
账号模块DRF序列化器
覆盖注册、登录、邀请、激活、角色委任、离职、注销租户、忘记密码、重置密码等业务
包含参数校验、权限判断、数据库事务、Redis限流、审计日志、Celery异步发邮件
"""

# 1. Python内置
import secrets
import random
from datetime import timedelta

# 2. Django
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db.models.functions import Concat
from django.db.models import Value, F

# 3. DRF
from rest_framework import serializers

# 4. 当前app自定义代码
from .models import Tenant, User, BannedEmail, AuditLog
from common.redis_utils import redis_client
from common.permissions import check_tenant_active
from .utils import expire_pending_user, get_role_full_name
from .tasks import send_invite_email, send_tenant_cancel_email, send_assign_role_email

class SendCodeSerializer(serializers.Serializer):
    """企业注册-发送验证码序列化器：校验邮箱黑名单、重复注册，Redis限流+存储验证码"""
    email = serializers.EmailField(label="邮箱")

    def validate_email(self, value):
        # 1. 检查邮箱是否被平台封禁
        if BannedEmail.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError('该邮箱已被平台封禁，请联系客服')
        # 2. 检查邮箱是否已被注册
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def save(self, **kwargs):
        email = self.validated_data['email']

        # 3. 检查60秒限流
        if redis_client.exists(f'reg_code_limit:{email}'):
            raise serializers.ValidationError('验证码已发送，请 60 秒后再试')

        # 生成6位数字验证码
        code = ''.join(random.choices('0123456789', k=6))
        
        # 4. 使用 Pipeline 打包写入Redis，原子操作
        pipe = redis_client.pipeline()
        pipe.set(f'reg_code_limit:{email}', '1', ex=60, nx=True)
        pipe.setex(f'reg_code:{email}', 300, code)
        result = pipe.execute()

        if not result[0]:
            raise serializers.ValidationError('操作过于频繁，请稍后再试')

        # 5. 发送邮件，发送失败则回滚Redis
        try:
            send_mail(
                subject='【企业知识库】注册验证码',
                message=f'您的验证码是：{code}，5 分钟内有效，请勿泄露给他人。',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            # 发送失败，清理Redis缓存
            redis_client.delete(f'reg_code_limit:{email}', f'reg_code:{email}')
            raise serializers.ValidationError('邮件发送失败，请稍后重试')

        return email

class RegisterSerializer(serializers.Serializer):
    """企业注册序列化器：校验验证码、密码一致性，事务内创建租户+董事账号"""
    company_name = serializers.CharField(max_length=100, label='公司名称')
    full_name = serializers.CharField(max_length=50, label='姓名')
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(min_length=6, write_only=True, label='密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')
    code = serializers.CharField(max_length=6, label='验证码')

    def validate_email(self, value):
        # 1. 检查邮箱是否被平台封禁
        if BannedEmail.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError('该邮箱已被封禁，无法注册')
        # 2. 检验邮箱是否未被注册
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def validate(self, attrs):
        # 3. 检验二次密码是否一致
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')
        # 4. 校验Redis中的验证码
        email = attrs['email']
        code = attrs['code']
        saved_code = redis_client.get(f'reg_code:{email}')
        if not saved_code or saved_code != code:
            raise serializers.ValidationError('验证码错误或已过期')
        return attrs

    def create(self, validated_data):
        # 开启事务，保证租户、用户创建原子性
        with transaction.atomic():
            # 1. 创建租户（公司）
            tenant = Tenant.objects.create(name=validated_data['company_name'])
            # 2. 创建董事账号，注册即激活，不需要邮件激活
            user = User.objects.create(
                tenant=tenant,
                email=validated_data['email'],
                original_email=validated_data['email'],
                password=make_password(validated_data['password']),
                full_name=validated_data['full_name'],
                role='owner',
                role_type='',
                status='active',
            )
            # 注册成功后删除验证码缓存
            redis_client.delete(f'reg_code:{validated_data["email"]}')
        return user

class LoginSerializer(serializers.Serializer):
    """统一登录序列化器：校验密码、账号封禁、账号状态、租户状态"""
    email = serializers.EmailField(label='邮箱')
    password = serializers.CharField(write_only=True, label='密码')

    def validate(self, attrs):
        email = attrs['email']
        password = attrs['password']
        # 1. 查询用户，预加载
        user = User.objects.select_related("tenant").filter(email=email).first()
        if not user:
            raise serializers.ValidationError('邮箱或密码错误')
        # 2. 校验密码哈希
        if not check_password(password, user.password):
            raise serializers.ValidationError('邮箱或密码错误')
        # 3. 校验全局黑名单
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
        # 5. 校验租户是否可用
        check_tenant_active(user)
        attrs['user'] = user
        return attrs

class UserListSerializer(serializers.ModelSerializer):
    """人员列表序列化器：展示角色、状态，动态计算前端按钮权限"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    can_operate = serializers.SerializerMethodField()
    can_assign_role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'full_name', 'role', 'role_type',
            'role_display', 'status', 'status_display',
            'invited_at', 'can_operate', 'can_assign_role'
        ]

    def get_can_operate(self, obj):
        """判断是否可以执行离职操作"""
        operator = self.context['request'].user
        # 在职才允许操作
        if obj.status != 'active':
            return False
        # 不能操作自己
        if operator.id == obj.id:
            return False
        # 董事可以操作除自己外非董事
        if operator.role == 'owner':
            return obj.role != 'owner'
        # HR只能操作普通员工
        if operator.role == 'admin' and operator.role_type == 'hr':
            return obj.role == 'member'
        return False

    def get_can_assign_role(self, obj):
        """判断是否可以执行委任角色操作"""
        operator = self.context['request'].user
        if obj.status != 'active':
            return False
        # 只有董事拥有委任权限
        if operator.role != 'owner':
            return False
        if operator.id == obj.id:
            return False
        if obj.role == 'owner':
            return False
        return True

class InviteSerializer(serializers.Serializer):
    """邀请用户序列化器：权限校验（董事/HR）、角色搭配校验、过期邀请邮箱释放、生成激活令牌"""
    email = serializers.EmailField(label='邮箱')
    full_name = serializers.CharField(max_length=50, label='姓名')
    role = serializers.ChoiceField(choices=['owner', 'admin', 'member'], label='系统角色')
    role_type = serializers.ChoiceField(choices=['hr', 'legal', ''], default='', required=False, label='管理员类型')

    def validate(self, attrs):
        request_user = self.context['request'].user
        check_tenant_active(request_user)
        is_owner = request_user.role == 'owner'
        is_hr = request_user.role == 'admin' and request_user.role_type == 'hr'
        if not (is_owner or is_hr):
            raise serializers.ValidationError('您没有权限邀请用户')
        # HR只能邀请员工
        if is_hr and attrs['role'] != 'member':
            raise serializers.ValidationError('HR 只能邀请员工')
        # 只有董事可以邀请董事
        if attrs['role'] == 'owner' and not is_owner:
            raise serializers.ValidationError('只有董事长可以邀请另一位董事长')
        # admin必须指定hr/legal
        if attrs['role'] == 'admin' and attrs['role_type'] not in ['hr', 'legal']:
            raise serializers.ValidationError('邀请管理员时必须指定 HR 或 法务')
        # member / owner 不能带role_type
        if attrs['role'] in ['member', 'owner'] and attrs['role_type'] != '':
            raise serializers.ValidationError('邀请员工时 role_type 必须为空')
        # 邮箱黑名单校验
        if BannedEmail.objects.filter(email=attrs['email'], is_active=True).exists():
            raise serializers.ValidationError('该邮箱已被封禁，无法邀请')
        # 邮箱占用处理：过期待激活账号自动释放邮箱
        existing_user = User.objects.filter(email=attrs['email']).first()
        if existing_user:
            if existing_user.status == 'pending' and existing_user.activation_token_expires_at and existing_user.activation_token_expires_at < timezone.now():
                timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
                existing_user.email = f'{existing_user.email}_expired_{timestamp}'
                existing_user.status = 'expired'
                existing_user.activation_token = ''
                existing_user.activation_token_expires_at = None
                existing_user.save(update_fields=['email', 'status', 'activation_token', 'activation_token_expires_at'])
            else:
                raise serializers.ValidationError('该邮箱已被注册或正在等待激活')
        return attrs

    def create(self, validated_data):
        request_user = self.context['request'].user
        company_name = request_user.tenant.name
        # 生成32位url安全激活令牌，24小时有效期
        token = secrets.token_urlsafe(32)
        with transaction.atomic():
            user = User.objects.create(
                tenant=request_user.tenant,
                email=validated_data['email'],
                original_email=validated_data['email'],
                full_name=validated_data['full_name'],
                password='',
                role=validated_data['role'],
                role_type=validated_data['role_type'],
                status='pending',
                activation_token=token,
                activation_token_expires_at=timezone.now() + timedelta(hours=24),
            )
            # 事务提交后异步发送邀请邮件
            transaction.on_commit(lambda: send_invite_email.delay(
                email=user.email,
                inviter_name=request_user.full_name,
                company_name=company_name,
                token=token,
            ))
        return user

class ActivateSerializer(serializers.Serializer):
    """账号激活序列化器：校验激活令牌、防重复激活，行锁保证并发安全"""
    token = serializers.CharField()
    password = serializers.CharField(min_length=6, write_only=True)
    confirm_password = serializers.CharField(min_length=6, write_only=True)

    def validate(self, attrs):
        token = attrs["token"]
        # Redis黑名单拦截已使用的token
        if redis_client.exists(f"activate_black:{token}"):
            raise serializers.ValidationError("该激活链接已使用或失效")
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')
        user = User.objects.select_related("tenant").filter(activation_token=token).first()
        if not user:
            raise serializers.ValidationError('无效的激活链接')
        if user.is_banned:
            raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')
        if user.tenant and user.tenant.status != 'active':
            raise serializers.ValidationError('所属公司已被平台停用')
        if user.status != 'pending':
            raise serializers.ValidationError('该账号已激活或链接已失效')
        # 过期自动处理
        now = timezone.now()
        if user.activation_token_expires_at < now:
            with transaction.atomic():
                expire_pending_user(user)
                redis_client.setex(f"activate_black:{token}", 300, "expired")
            raise serializers.ValidationError("激活链接已过期，请联系管理员重新邀请")
        attrs["user"] = user
        return attrs

    def create(self, validated_data):
        user = validated_data["user"]
        token = validated_data["token"]
        with transaction.atomic():
            # 行锁，防止并发重复激活
            locked_user = User.objects.select_for_update().select_related("tenant").filter(
                id=user.id,
                activation_token=validated_data['token']
            ).first()
            if not locked_user or locked_user.status != "pending" or locked_user.activation_token_expires_at < timezone.now():
                raise serializers.ValidationError("激活链接已失效，请重新获取邀请")
            # 设置密码、激活账号、清空激活令牌
            locked_user.password = make_password(validated_data['password'])
            locked_user.status = 'active'
            locked_user.activated_at = timezone.now()
            locked_user.activation_token = ''
            locked_user.activation_token_expires_at = None
            locked_user.save(update_fields=[
                "password", "status", "activated_at",
                "activation_token", "activation_token_expires_at"
            ])
            # 标记token已使用
            redis_client.setex(f"activate_black:{token}", 300, "used")
        return locked_user

class AssignRoleSerializer(serializers.Serializer):
    """角色委任序列化器：董事专属，更新角色、写入审计日志、异步发邮件通知"""
    user_id = serializers.IntegerField(label='目标用户ID')
    target_role = serializers.ChoiceField(choices=['admin', 'member'], label='目标角色')
    target_role_type = serializers.ChoiceField(choices=['hr', 'legal', ''], default='', required=False, label='管理员类型')

    def validate(self, attrs):
        operator = self.context['request'].user
        if operator.role != 'owner':
            raise serializers.ValidationError('只有董事长可以委任职位')
        target = User.objects.filter(
            id=attrs['user_id'],
            tenant=operator.tenant,
        ).first()
        if not target:
            raise serializers.ValidationError('用户不存在或不属于本公司')
        if target.id == operator.id:
            raise serializers.ValidationError('不能委任自己')
        if target.role == 'owner':
            raise serializers.ValidationError('董事长之间无法互相委任')
        if target.status != 'active':
            raise serializers.ValidationError('只能委任在职用户')
        if (target.role == attrs['target_role'] and target.role_type == attrs['target_role_type']):
            raise serializers.ValidationError('该用户已是此角色')
        # 角色搭配校验
        if attrs['target_role'] == 'admin' and attrs['target_role_type'] not in ['hr', 'legal']:
            raise serializers.ValidationError('委任为管理员时必须指定 HR 或法务')
        if attrs['target_role'] == 'member' and attrs['target_role_type'] != '':
            raise serializers.ValidationError('委任为员工时 role_type 必须为空')
        attrs['target'] = target
        return attrs

    def save(self, **kwargs):
        target = self.validated_data['target']
        operator = self.context['request'].user
        old_role = target.role
        old_role_type = target.role_type
        old_role_name = get_role_full_name(old_role, old_role_type)
        with transaction.atomic():
            locked_target = User.objects.select_for_update().filter(
                id=target.id,
                tenant=operator.tenant
            ).first()
            if not locked_target or locked_target.status != "active" or locked_target.role == "owner":
                raise serializers.ValidationError("用户状态发生变更，操作失败")
            locked_target.role = self.validated_data['target_role']
            locked_target.role_type = self.validated_data['target_role_type']
            locked_target.save(update_fields=['role', 'role_type'])
            new_role_name = get_role_full_name(locked_target.role, locked_target.role_type)
            # 写入审计日志
            AuditLog.objects.create(
                tenant=operator.tenant,
                operator=operator,
                target_user=target,
                action='assign_role',
                detail=f'职位从 {old_role_name} 改为 {new_role_name}',
            )
            # 事务提交后异步发邮件
            transaction.on_commit(lambda: send_assign_role_email.delay(
                email=target.email,
                full_name=target.full_name,
                old_role=old_role_name,
                new_role=new_role_name,
                company_name=operator.tenant.name,
            ))
        return locked_target

class ResignUserSerializer(serializers.Serializer):
    """管理员操作员工离职序列化器：董事/HR权限，离职后邮箱加后缀释放邮箱唯一索引"""
    user_id = serializers.IntegerField(label='目标用户ID')

    def validate(self, attrs):
        operator = self.context['request'].user
        target = User.objects.filter(
            id=attrs['user_id'],
            tenant=operator.tenant,
        ).first()
        if not target:
            raise serializers.ValidationError('用户不存在或不属于本公司')
        if target.id == operator.id:
            raise serializers.ValidationError('不能操作自己')
        is_owner = operator.role == 'owner'
        is_hr = operator.role == 'admin' and operator.role_type == 'hr'
        if not (is_owner or is_hr):
            raise serializers.ValidationError('您没有权限操作')
        if is_hr and target.role != 'member':
            raise serializers.ValidationError('HR 只能操作普通员工')
        if is_owner and target.role == 'owner':
            raise serializers.ValidationError('董事长之间无法互相操作')
        if target.status != 'active':
            raise serializers.ValidationError('账号状态异常，无法离职')
        attrs['target'] = target
        return attrs

    def save(self, **kwargs):
        target = self.validated_data['target']
        target_id = target.id
        operator = self.context['request'].user
        tenant = operator.tenant
        with transaction.atomic():
            target = User.objects.select_for_update().get(id=target_id, tenant=tenant)
            if target.status != 'active':
                raise serializers.ValidationError('账号状态异常，无法离职')
            old_email = target.email
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            target.email = f'{target.email}_resigned_{timestamp}'
            target.status = 'resigned'
            target.save(update_fields=['email', 'status'])
            role_name = get_role_full_name(target.role, target.role_type)
            AuditLog.objects.create(
                tenant=operator.tenant,
                operator=operator,
                target_user=target,
                action='resign',
                detail=f'{target.full_name}（{role_name}）离职，原邮箱 {old_email}',
            )
        return target

class SelfResignSerializer(serializers.Serializer):
    """董事主动离职序列化器：校验至少保留一名在职董事，租户行锁保证并发安全"""
    def validate(self, attrs):
        user = self.context['request'].user
        if user.role != 'owner':
            raise serializers.ValidationError('仅限董事使用')
        if user.status != 'active':
            raise serializers.ValidationError('账号状态异常，无法离职')
        # 校验至少保留一名在职董事
        other_owners = User.objects.filter(
            tenant=user.tenant,
            role='owner',
            status='active',
        ).exclude(id=user.id)
        if not other_owners.exists():
            raise serializers.ValidationError('公司至少需要保留一位董事，请先委任新董事')
        attrs['user_id'] = user.id
        attrs['tenant_id'] = user.tenant_id
        return attrs

    def save(self, **kwargs):
        user_id = self.validated_data['user_id']
        tenant_id = self.validated_data['tenant_id']
        with transaction.atomic():
            # 锁住租户行，防止并发离职导致无董事
            Tenant.objects.select_for_update().get(id=tenant_id)
            locked_user = User.objects.get(id=user_id, tenant_id=tenant_id)
            other_owners = User.objects.filter(
                tenant_id=tenant_id,
                role='owner',
                status='active',
            ).exclude(id=user_id)
            if not other_owners.exists():
                raise serializers.ValidationError('公司至少需要保留一位董事，请先委任新董事')
            if locked_user.status != 'active':
                raise serializers.ValidationError('账号状态异常，无法离职')
            old_email = locked_user.email
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            locked_user.email = f'{locked_user.email}_resigned_{timestamp}'
            locked_user.status = 'resigned'
            locked_user.save(update_fields=['email', 'status'])
            AuditLog.objects.create(
                tenant_id=tenant_id,
                operator=locked_user,
                target_user=locked_user,
                action='resign',
                detail=f'董事 {locked_user.full_name} 主动离职，原邮箱 {old_email}',
            )
        return locked_user

class CancelTenantSerializer(serializers.Serializer):
    """董事注销公司序列化器：密码二次校验，批量更新租户下所有用户状态、写审计日志、通知全部董事"""
    password = serializers.CharField(write_only=True, label='登录密码')

    def validate(self, attrs):
        user = self.context['request'].user
        if user.role != 'owner':
            raise serializers.ValidationError('只有董事可以注销公司')
        if user.status != 'active':
            raise serializers.ValidationError('账号状态异常，无法注销公司')
        if not check_password(attrs['password'], user.password):
            raise serializers.ValidationError('密码错误')
        tenant = user.tenant
        if tenant.status == 'cancelled':
            raise serializers.ValidationError('公司已注销')
        if tenant.status == 'banned':
            raise serializers.ValidationError('已封禁的企业无法注销，请联系平台客服')
        attrs['user_id'] = user.id
        attrs['tenant_id'] = user.tenant_id
        return attrs

    def save(self, **kwargs):
        user_id = self.validated_data['user_id']
        tenant_id = self.validated_data['tenant_id']
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(id=tenant_id)
            locked_user = User.objects.get(id=user_id, tenant_id=tenant_id)
            if tenant.status == 'cancelled':
                raise serializers.ValidationError('公司已注销')
            if tenant.status == 'banned':
                raise serializers.ValidationError('已封禁的企业无法注销，请联系平台客服')
            if locked_user.status != 'active':
                raise serializers.ValidationError('账号状态已变更，操作失败')
            # 收集董事邮箱用于通知
            owners = list(
                User.objects.filter(
                    tenant=tenant,
                    role='owner',
                    status__in=['active', 'pending'],
                ).values('full_name', 'original_email', 'email')
            )
            owners_data = [
                {
                    'full_name': o['full_name'],
                    'email': o['original_email'] or o['email'],
                }
                for o in owners
            ]
            # 修改租户状态为注销
            tenant.status = 'cancelled'
            tenant.save(update_fields=['status'])
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            # 批量更新在职用户
            active_count = User.objects.filter(tenant=tenant, status='active').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
                status='resigned',
                activation_token='',
                activation_token_expires_at=None
            )
            # 批量更新待激活用户
            pending_count = User.objects.filter(tenant=tenant, status='pending').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
                status='expired',
                activation_token='',
                activation_token_expires_at=None
            )
            # 批量更新已过期用户
            expired_count = User.objects.filter(tenant=tenant, status='expired').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
            )
            # 写入审计日志
            total_affected = active_count + pending_count + expired_count
            AuditLog.objects.create(
                tenant=tenant,
                operator=locked_user,
                target_user=locked_user,
                action='cancel_tenant',
                detail=f'董事 {locked_user.full_name} 注销公司「{tenant.name}」，'
                       f'影响 {active_count} 名在职、{pending_count} 名待激活、'
                       f'{expired_count} 名已过期用户',
            )
            # 异步通知全部董事
            transaction.on_commit(lambda: send_tenant_cancel_email.delay(
                owners=owners_data,
                company_name=tenant.name,
            ))
        return tenant

class ForgotPasswordSerializer(serializers.Serializer):
    """忘记密码-发送验证码序列化器：60秒限流，用户不存在也返回同样限流，防止枚举邮箱"""
    email = serializers.EmailField(label='邮箱')

    def validate_email(self, value):
        if redis_client.exists(f'reset_limit:{value}'):
            raise serializers.ValidationError('验证码已发送，请 60 秒后再试')
        return value

    def save(self, **kwargs):
        email = self.validated_data['email']
        user = User.objects.filter(email=email).first()
        code = ''.join(random.choices('0123456789', k=6))
        pipe = redis_client.pipeline()
        if user and not user.is_banned:
            pipe.set(f'reset_limit:{email}', '1', ex=60, nx=True)
            pipe.setex(f'reset_code:{email}', 300, code)
            result = pipe.execute()
            if result[0]:
                try:
                    send_mail(
                        subject='【知识库系统】密码重置验证码',
                        message=f'您的密码重置验证码是：{code}，5 分钟内有效。\n'
                                f'如非本人操作，请忽略此邮件。',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=False,
                    )
                except Exception:
                    redis_client.delete(f'reset_limit:{email}', f'reset_code:{email}')
                    raise serializers.ValidationError('邮件发送失败，请稍后重试')
        else:
            # 用户不存在/封禁，依旧设置限流，防止枚举
            pipe.set(f'reset_limit:{email}', '1', ex=60, nx=True)
            pipe.execute()
        return user

class ResetPasswordSerializer(serializers.Serializer):
    """重置密码序列化器：验证码校验、行锁、密码哈希更新"""
    email = serializers.EmailField(label='邮箱')
    code = serializers.CharField(max_length=6, label='验证码')
    password = serializers.CharField(min_length=6, write_only=True, label='新密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')
        email = attrs['email']
        code = attrs['code']
        saved_code = redis_client.get(f'reset_code:{email}')
        if not saved_code or saved_code != code:
            raise serializers.ValidationError('验证码错误或已过期')
        return attrs

    def save(self, **kwargs):
        email = self.validated_data['email']
        new_password = self.validated_data['password']
        with transaction.atomic():
            user = User.objects.select_for_update().filter(email=email).first()
            if not user:
                raise serializers.ValidationError('用户不存在')
            if user.is_banned:
                raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')
            if user.status not in ['active', 'pending']:
                raise serializers.ValidationError('账号状态异常，无法重置密码')
            # 二次校验验证码，防止并发
            pipe = redis_client.pipeline()
            pipe.get(f'reset_code:{email}')
            pipe.delete(f'reset_code:{email}')
            res = pipe.execute()
            real_code = res[0]
            if not real_code or real_code != self.validated_data['code']:
                raise serializers.ValidationError('验证码错误或已过期')
            user.password = make_password(new_password)
            user.save(update_fields=['password'])
        return user
