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
from .utils import redis_client, check_tenant_active, expire_pending_user
from .tasks import send_invite_email, send_tenant_cancel_email, send_assign_role_email

# 企业注册-发送验证码 序列化器
class SendCodeSerializer(serializers.Serializer):
    email = serializers.EmailField(label="邮箱")

    def validate_email(self, value):
        # 1. 检查邮箱是否被平台封禁
        if BannedEmail.objects.filter(email=value).exists():
            raise serializers.ValidationError('该邮箱已被平台封禁，请联系客服')
        # 2. 检查邮箱是否已被注册
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def save(self, **kwargs):
        email = self.validated_data['email']

        # 3. 检查限流
        if redis_client.exists(f'reg_code_limit:{email}'):
            raise serializers.ValidationError('验证码已发送，请 60 秒后再试')

        code = ''.join(random.choices('0123456789', k=6))
        
        # 4. 使用 Pipeline 打包写入 Redis
        pipe = redis_client.pipeline()
        pipe.set(f'reg_code_limit:{email}', '1', ex=60, nx=True)
        pipe.setex(f'reg_code:{email}', 300, code)
        result = pipe.execute()

        if not result[0]:
            raise serializers.ValidationError('操作过于频繁，请稍后再试')

        # 5. 发送邮件，失败则回滚 Redis
        try:
            send_mail(
                subject='【企业知识库】注册验证码',
                message=f'您的验证码是：{code}，5 分钟内有效，请勿泄露给他人。',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            # 发送失败，删除刚写入的 Redis
            redis_client.delete(f'reg_code_limit:{email}', f'reg_code:{email}')
            raise serializers.ValidationError('邮件发送失败，请稍后重试')

        return email

# 企业注册 序列化器
class RegisterSerializer(serializers.Serializer):
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

        # 4. 校验验证码
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
        user = User.objects.select_related("tenant").filter(email=email).first()
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

        company_name = request_user.tenant.name

        # 生成激活令牌，24 小时有效
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

        transaction.on_commit(lambda: send_invite_email.delay(
            email=user.email,
            inviter_name=request_user.full_name,
            company_name=company_name,
            token=token,
        ))

        return user

# 激活账号 序列化器
class ActivateSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(min_length=6, write_only=True)
    confirm_password = serializers.CharField(min_length=6, write_only=True)

    def validate(self, attrs):
        token = attrs["token"]
        # 1. Redis黑名单前置拦截
        if redis_client.exists(f"activate_black:{token}"):
            raise serializers.ValidationError("该激活链接已使用或失效")

        # 2. 校验两次密码是否一致
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')

        # 预查询用户
        user = User.objects.select_related("tenant").filter(activation_token=token).first()

        # 3. 判断激活码是否有效
        if not user:
            raise serializers.ValidationError('无效的激活链接')

        # 4. 判断账号是否被封禁
        if user.is_banned:
            raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')

        # 5. 判断所属公司是否被停用
        if user.tenant and user.tenant.status != 'active':
            raise serializers.ValidationError('所属公司已被平台停用')

        # 6. 判断账号状态是否处于待激活状态
        if user.status != 'pending':
            raise serializers.ValidationError('该账号已激活或链接已失效')

        # 7. 判断激活码是否过期
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
            # 加行锁，防止一个邀请码被两个人同时激活，导致数据异常
            locked_user = User.objects.select_for_update().select_related("tenant").filter(
                id=user.id,
                activation_token=validated_data['token']
            ).first()

            if not locked_user or locked_user.status != "pending" or locked_user.activation_token_expires_at < timezone.now():
                raise serializers.ValidationError("激活链接已失效，请重新获取邀请")
            
            # 数据库更新：设置密码、激活账号、清空激活码
            locked_user.password = make_password(validated_data['password'])
            locked_user.status = 'active'
            locked_user.activated_at = timezone.now()
            locked_user.activation_token = ''
            locked_user.activation_token_expires_at = None
            locked_user.save(update_fields=[
                "password",
                "status",
                "activated_at",
                "activation_token",
                "activation_token_expires_at"
            ])
            
        redis_client.setex(f"activate_black:{token}", 300, "used")
        return locked_user

# 角色委任 序列化器
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
            tenant=operator.tenant,
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
            # 加行锁
            locked_target = User.objects.select_for_update().filter(
                id=target.id,
                tenant=operator.tenant
            ).first()

            if not locked_target or locked_target.status != "active" or locked_target.role == "owner":
                raise serializers.ValidationError("用户状态发生变更，操作失败")

            # 更新角色信息
            locked_target.role = self.validated_data['target_role']
            locked_target.role_type = self.validated_data['target_role_type']
            locked_target.save(update_fields=['role', 'role_type'])

            # 写入审计日志
            AuditLog.objects.create(
                tenant=operator.tenant,
                operator=operator,
                target_user=target,
                action='assign_role',
                detail=f'角色从 {old_role}/{old_role_type or "无"} '
                       f'改为 {target.role}/{target.role_type or "无"}',
            )

            # 事务提交后发邮件
            transaction.on_commit(lambda: send_assign_role_email.delay(
                email=target.email,
                full_name=target.full_name,
                old_role=old_role,
                new_role=target.get_role_display(),
                company_name=operator.tenant.name,
            ))

        return locked_target

# 员工离职 序列化器
class ResignUserSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(label='目标用户ID')
    
    def validate(self, attrs):
        operator = self.context['request'].user

        # 1. 只能操作本公司人员
        target = User.objects.filter(
            id=attrs['user_id'],
            tenant=operator.tenant,
        ).first()
        if not target:
            raise serializers.ValidationError('用户不存在或不属于本公司')

        # 2. 禁止操作自己
        if target.id == operator.id:
            raise serializers.ValidationError('不能操作自己')

        # 3. 判断操作者身份：董事长 或者 HR
        is_owner = operator.role == 'owner'
        is_hr = operator.role == 'admin' and operator.role_type == 'hr'
        if not (is_owner or is_hr):
            raise serializers.ValidationError('您没有权限操作')

        # 4. HR只能操作员工，不能操作董事长、管理员
        if is_hr and target.role != 'member':
            raise serializers.ValidationError('HR 只能操作普通员工')

        # 5. 董事长不能离职其他董事长
        if is_owner and target.role == 'owner':
            raise serializers.ValidationError('董事长之间无法互相操作')

        # 6. 只能操作在职用户
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
            
            # 写审计日志
            AuditLog.objects.create(
                tenant=operator.tenant,
                operator=operator,
                target_user=target,
                action='resign',
                detail=f'{target.full_name}（{target.get_role_display()}）离职，原邮箱 {old_email}',
            )
        return target

# 董事离职 序列化器
class SelfResignSerializer(serializers.Serializer):
    def validate(self, attrs):
        user = self.context['request'].user

        # 1. 必须是董事长
        if user.role != 'owner':
            raise serializers.ValidationError('仅限董事使用')

        # 2. 必须是在职状态
        if user.status != 'active':
            raise serializers.ValidationError('账号状态异常，无法离职')
        
        # 3. 至少保留一名在职董事
        other_owners = User.objects.filter(
            tenant=user.tenant,
            role='owner',
            status='active',
        ).exclude(id=user.id)
        if not other_owners.exists():
            raise serializers.ValidationError(
                '公司至少需要保留一位董事，请先委任新董事'
            )

        attrs['user_id'] = user.id
        attrs['tenant_id'] = user.tenant_id
        return attrs

    def save(self, **kwargs):
        user_id = self.validated_data['user_id']
        tenant_id = self.validated_data['tenant_id']

        with transaction.atomic():
            # 锁住租户行，保证同一租户的董事正常离职
            Tenant.objects.select_for_update().get(id=tenant_id)
            locked_user = User.objects.get(id=user_id, tenant_id=tenant_id)
            
            # 至少保留一名在职董事
            other_owners = User.objects.filter(
                tenant_id=tenant_id,
                role='owner',
                status='active',
            ).exclude(id=user_id)
            if not other_owners.exists():
                raise serializers.ValidationError('公司至少需要保留一位董事，请先委任新董事')

            if locked_user.status != 'active':
                raise serializers.ValidationError('账号状态异常，无法离职')

            # 1. 释放董事邮箱
            old_email = locked_user.email
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            locked_user.email = f'{locked_user.email}_resigned_{timestamp}'

            # 2. 改董事状态
            locked_user.status = 'resigned'
            locked_user.save(update_fields=['email', 'status'])

            # 3. 写审计日志
            AuditLog.objects.create(
                tenant_id=tenant_id,
                operator=locked_user,
                target_user=locked_user,
                action='resign',
                detail=f'董事 {locked_user.full_name} 主动离职，原邮箱 {old_email}',
            )
        return locked_user
    
# 董事注销公司 序列化器
class CancelTenantSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, label='登录密码')

    def validate(self, attrs):
        user = self.context['request'].user

        # 1. 必须是董事
        if user.role != 'owner':
            raise serializers.ValidationError('只有董事可以注销公司')

        # 2. 账号必须是在职状态
        if user.status != 'active':
            raise serializers.ValidationError('账号状态异常，无法注销公司')

        # 3. 二次确认密码
        if not check_password(attrs['password'], user.password):
            raise serializers.ValidationError('密码错误')

        # 4. 租户状态校验
        tenant = user.tenant
        if user.tenant.status == 'cancelled':
            raise serializers.ValidationError('公司已注销')
        if user.tenant.status == 'banned':
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

            # 1. 二次校验注销
            if tenant.status == 'cancelled':
                raise serializers.ValidationError('公司已注销')
            if tenant.status == 'banned':
                raise serializers.ValidationError('已封禁的企业无法注销，请联系平台客服')
            if locked_user.status != 'active':
                raise serializers.ValidationError('账号状态已变更，操作失败')

            # 2. 收集所有董事信息
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

            # 3. 改租户状态
            tenant.status = 'cancelled'
            tenant.save(update_fields=['status'])

            # 4. 清理所有未离职的用户
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            # active 用户 → resigned
            active_count = User.objects.filter(tenant=tenant, status='active').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
                status='resigned',
                activation_token='',
                activation_token_expires_at=None
            )
            
            # pending 用户 → expired + 清空激活令牌
            pending_count = User.objects.filter(tenant=tenant, status='pending').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
                status='expired',
                activation_token='',
                activation_token_expires_at=None
            )

            # expired 用户 → 释放邮箱
            expired_count = User.objects.filter(tenant=tenant, status='expired').update(
                email=Concat(F('email'), Value(f'_resigned_{timestamp}')),
            )

            # 5. 写审计日志
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

            # 6. 邮件通知所有董事
            transaction.on_commit(lambda: send_tenant_cancel_email.delay(
                owners=owners_data,
                company_name=tenant.name,
                ))
            
        return tenant

# 用户忘记密码 发送验证码
class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(label='邮箱')

    def validate_email(self, value):
        # 60秒内只能发一次
        if redis_client.exists(f'reset_limit:{value}'):
            raise serializers.ValidationError('验证码已发送，请 60 秒后再试')
        return value

    def save(self, **kwargs):
        email = self.validated_data['email']
        user = User.objects.filter(email=email).first()
        code = ''.join(random.choices('0123456789', k=6))
        pipe = redis_client.pipeline()

        # "用户存在 + 未被封禁" 发邮件
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
            pipe.set(f'reset_limit:{email}', '1', ex=60, nx=True)
            pipe.execute()

        return user
    
# 重置密码 序列化器
class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(label='邮箱')
    code = serializers.CharField(max_length=6, label='验证码')
    password = serializers.CharField(min_length=6, write_only=True, label='新密码')
    confirm_password = serializers.CharField(min_length=6, write_only=True, label='确认密码')

    def validate(self, attrs):

        # 1. 两次密码一致
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')

        # 2. 验证码校验
        email = attrs['email']
        code = attrs['code']
        saved_code = redis_client.get(f'reset_code:{email}')

        if not saved_code or saved_code != code:
            raise serializers.ValidationError('验证码错误或已过期')

        # 3. 查用户
        user = User.objects.filter(email=email).first()
        if not user:
            raise serializers.ValidationError('用户不存在')

        # 4. 平台封禁校验
        if user.is_banned:
            raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')

        # 5. 账号状态校验 只有 active 或 pending 才能重置密码
        if user.status not in ['active', 'pending']:
            raise serializers.ValidationError('账号状态异常，无法重置密码')

        attrs['user_id'] = user.id
        attrs['email'] = email
        return attrs

    def save(self, **kwargs):
        user_id = self.validated_data['user_id']
        email = self.validated_data['email']
        new_password = self.validated_data['password']

        with transaction.atomic():
            locked_user = User.objects.get(id=user_id)

            # 二次校验
            if locked_user.is_banned:
                raise serializers.ValidationError('账号已被平台封禁，请联系平台客服')
            if locked_user.status not in ['active', 'pending']:
                raise serializers.ValidationError('账号状态异常，无法重置密码')
            
            pipe = redis_client.pipeline()
            pipe.get(f'reset_code:{email}')
            pipe.delete(f'reset_code:{email}')
            res = pipe.execute()
            real_code = res[0]
            if not real_code or real_code != self.validated_data['code']:
                raise serializers.ValidationError('验证码错误或已过期')

            # 更新密码
            locked_user.password = make_password(new_password)
            locked_user.save(update_fields=['password'])
        return locked_user
