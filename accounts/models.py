from django.db import models

# tenants 租户表
class Tenant(models.Model):
    # 企业名称
    name = models.CharField(max_length=100, verbose_name='企业名称')
    # 企业状态
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    # 注册时间
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='注册时间')

    class Meta:
        # 指定数据库该表：tenants
        db_table = 'tenants'
        verbose_name = '租户'
        verbose_name_plural = '租户'

    def __str__(self):
        return self.name

# user 用户表
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
        ('resigned', '已离职'),
    ]

    # tenant_id
    tenant = models.ForeignKey('Tenant', on_delete=models.PROTECT)
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
        return self.status == 'active'

    @property
    def is_anonymous(self):
        return False

    @property
    def is_authenticated(self):
        return True

    def get_username(self):
        return self.email

    class Meta:
        db_table = 'users'
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return f'{self.full_name}({self.get_role_display()})'