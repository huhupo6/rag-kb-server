from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.hashers import check_password as django_check_password

class UserManager(BaseUserManager):
    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD: username})

    def create_superuser(self, email, password=None, **extra_fields):
        """创建并保存一个平台超级管理员"""
        if not email:
            raise ValueError('超级管理员必须提供邮箱')
        if not password:
            raise ValueError('超级管理员必须提供密码')

        # 设置平台超管的默认属性
        extra_fields.setdefault('tenant', None)       # 平台超管不属于任何租户
        extra_fields.setdefault('role', 'owner')      # 角色为董事
        extra_fields.setdefault('role_type', '')      # 无具体职务
        extra_fields.setdefault('status', 'active')   # 直接激活状态
        extra_fields.setdefault('full_name', '平台管理员') # 默认姓名

        # 实例化用户
        user = self.model(email=self.normalize_email(email), **extra_fields)
        
        # 进行密码加密
        user.set_password(password)
        user.save(using=self._db)
        return user

# tenants 租户表
class Tenant(models.Model):
    STATUS_CHOICES = [
        ('active', '正常使用'),      # 正常使用
        ('suspended', '暂停服务'),   # 欠费
        ('banned', '已封禁'),        # 违规被封禁
        ('cancelled', '已注销'),     # 企业主动注销
    ]
    # 企业名称
    name = models.CharField(max_length=100, verbose_name='企业名称')
    # 企业状态
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='active', 
        verbose_name='租户状态' 
    )
    # 注册时间
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='注册时间')

    class Meta:
        # 指定数据库该表：tenants
        db_table = 'tenants'
        verbose_name = '租户'
        verbose_name_plural = '租户'

    def __str__(self):
        return self.name

# users 用户表
class User(models.Model):
    # role：一级权限
    ROLE_CHOICES = [
        ('owner', '董事'), 
        ('admin', '管理员'), 
        ('member', '员工'), 
    ]

    # role_type：二级权限
    ROLE_TYPE_CHOICES = [
    ('hr', 'HR'), 
    ('legal', '法务'), 
    ('', '无'), 
    ]

    # status：账号状态
    STATUS_CHOICES = [
        ('pending', '待激活'),
        ('active', '在职'),
        ('resigned', '已离职'),  # 默认注销 = 已离职
        ('expired', '邀请已过期'),
    ]

    # tenant_id
    tenant = models.ForeignKey(
        'Tenant', 
        on_delete=models.PROTECT, 
        null=True, blank=True, 
        verbose_name='所属公司'
        )
    # 邮箱
    email = models.EmailField(unique=True, verbose_name='邮箱')
    # 原始邮箱
    original_email = models.EmailField(blank=True, verbose_name='原始邮箱')
    # 密码
    password = models.CharField(max_length=128, blank=True, verbose_name='密码')
    # 姓名
    full_name = models.CharField(max_length=50, verbose_name='姓名')

    # 角色与权限，默认为员工
    role = models.CharField(
        max_length=20, 
        choices=ROLE_CHOICES, 
        default='member', 
        verbose_name='系统角色'
        )
    role_type = models.CharField(
        max_length=20, 
        choices=ROLE_TYPE_CHOICES, 
        blank=True, default='', 
        verbose_name='管理员类型'
        )
    
    # 账号状态，默认不可用
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',      # 待激活
        verbose_name='账号状态'
        )
    
    # 激活令牌，只用一次
    activation_token = models.CharField(max_length=64, blank=True, verbose_name='激活令牌')
    # 令牌过期时间
    activation_token_expires_at = models.DateTimeField(
        null=True, blank=True, 
        verbose_name='令牌过期时间'
        )
    # 邀请时间
    invited_at = models.DateTimeField(auto_now_add=True, verbose_name='邀请时间')
    # 激活时间
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name='激活时间')

    # 绑定自定义 Manager
    objects = UserManager()

    # Django 认证系统需要的类属性
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def check_password(self, raw_password):
        return django_check_password(raw_password, self.password)

    def set_password(self, raw_password):
        from django.contrib.auth.hashers import make_password
        self.password = make_password(raw_password)

    def get_username(self):
        return self.email
    
    def get_full_name(self):
        return self.full_name
    
    def get_short_name(self):
        return self.full_name

    # 平台管理员拥有全部权限
    def has_perm(self, perm, obj=None):
        if self.is_superuser:
            return True
        return False

    # 平台管理员可以访问所有模块
    def has_module_perms(self, app_label):
        if self.is_superuser:
            return True
        return False

    # 实时查询 BannedEmail 表，判断当前邮箱是否被封禁
    @property
    def is_banned(self):
        emails = [self.email]
        if self.original_email:
            emails.append(self.original_email)
        return BannedEmail.objects.filter(email__in=emails, is_active=True).exists()

    @property
    def is_active(self):
        return self.status == 'active' and not self.is_banned

    @property
    def is_anonymous(self):
        return False

    @property
    def is_authenticated(self):
        return True

    @property
    def is_staff(self):
        return self.role == 'owner' and self.tenant is None

    @property
    def is_superuser(self):
        return self.is_staff

    @property
    def real_email(self):
        return self.original_email or self.email

    class Meta:
        db_table = 'users'
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return f'{self.full_name}({self.get_role_display()})'

# banned_emails 封禁邮箱表
class BannedEmail(models.Model):
    email = models.EmailField(unique=True, verbose_name='被封禁的邮箱')
    banned_at = models.DateTimeField(auto_now_add=True, verbose_name='封禁时间')
    is_active = models.BooleanField(default=True, verbose_name='是否生效')
    unbanned_at = models.DateTimeField(null=True, blank=True, verbose_name='解封时间')


    class Meta:
        db_table = 'banned_emails'
        verbose_name = '平台封禁邮箱'
        verbose_name_plural = '平台封禁邮箱'

    def __str__(self):
        if self.is_active:
            status = '生效中'
        else:
            status = '已解封'
        return f'{self.email}（{status}）'

# audit_logs 审计日志表
class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('assign_role', '委任角色'),
        ('resign', '离职'),
        ('invite', '邀请'),
        ('ban', '平台封禁'),
        ('unban', '平台解封'),
        ('self_reset_password', '自助重置密码'),
        ('cancel_tenant', '公司注销'),
        ('delete_document', '删除文档链'),
    ]

    tenant = models.ForeignKey(
        'Tenant',
        on_delete=models.PROTECT,
        null=True, blank=True,
        verbose_name='所属公司'
    )
    operator = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='operated_logs',
        verbose_name='操作者'
    )
    target_user = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='targeted_logs',
        verbose_name='被操作者'
    )
    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES,
        verbose_name='操作类型'
    )
    detail = models.TextField(blank=True, verbose_name='操作详情')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='操作时间')

    class Meta:
        db_table = 'audit_logs'
        verbose_name = '审计日志'
        verbose_name_plural = '审计日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'created_at'], name='audit_tenant_time_idx'),
        ]

    def __str__(self):
        return f'{self.operator} {self.get_action_display()} {self.target_user}'
