from django.db import models

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
    # role：系统权限角色
    ROLE_CHOICES = [
        ('owner', '董事'),
        ('admin', '管理员'),
        ('member', '员工'),
    ]

    # role_type：业务岗位标签
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
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member', verbose_name='系统角色')
    role_type = models.CharField(max_length=20, choices=ROLE_TYPE_CHOICES, blank=True, default='', verbose_name='管理员类型')
    
    # 账号状态，默认不可用
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='账号状态')
    # 平台封禁
    is_banned = models.BooleanField(default=False, verbose_name='是否被平台封禁')
    banned_at = models.DateTimeField(null=True, blank=True, verbose_name='封禁时间')
    
    # 激活令牌，只用一次
    activation_token = models.CharField(max_length=64, blank=True, verbose_name='激活令牌')
    # 令牌过期时间
    activation_token_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name='令牌过期时间'
        )
    # 邀请时间
    invited_at = models.DateTimeField(auto_now_add=True, verbose_name='邀请时间')
    # 激活时间
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name='激活时间')

    # Django 认证系统需要的类属性
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    @property
    def is_active(self):
        return self.status == 'active' and not self.is_banned

    @property
    def is_anonymous(self):
        return False

    @property
    def is_authenticated(self):
        return True

    def get_username(self):
        return self.email

    def has_perm(self, perm, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False

    class Meta:
        db_table = 'users'
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return f'{self.full_name}({self.get_role_display()})'