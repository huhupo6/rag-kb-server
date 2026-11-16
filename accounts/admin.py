"""
accounts/admin.py
账号模块Django后台管理配置
提供租户、用户、封禁邮箱、审计日志的后台页面与批量操作Action
支持租户启停封、用户黑名单管理、审计日志只读查看
"""

# Django内置
from django.contrib import admin
from django.contrib.auth.models import Group
from django.db.models import Exists, OuterRef
from django.utils import timezone

# 当前app模型
from .models import Tenant, User, BannedEmail, AuditLog

# 移除默认的用户组后台管理入口，本项目不使用Group
admin.site.unregister(Group)


@admin.action(description='解封选中邮箱')
def unban_emails(modeladmin, request, queryset):
    """
    批量解封封禁邮箱
    将选中、处于生效封禁状态的邮箱标记为解除封禁，记录解封时间
    """
    active_qs = queryset.filter(is_active=True)
    emails = list(active_qs.values_list('email', flat=True))
    count = active_qs.update(is_active=False, unbanned_at=timezone.now())
    if count:
        modeladmin.message_user(request, f'已解封 {count} 个邮箱：{", ".join(emails)}')
    else:
        modeladmin.message_user(request, '选中的记录中没有生效中的封禁')


@admin.action(description='封禁选中用户（加入平台黑名单）')
def ban_users(modeladmin, request, queryset):
    """
    批量封禁用户
    根据用户真实邮箱，在BannedEmail黑名单表新增/更新封禁记录
    """
    add_count = 0
    for user in queryset:
        email_to_ban = user.real_email
        obj, created = BannedEmail.objects.update_or_create(
            email=email_to_ban,
            defaults={
                'is_active': True,
                'unbanned_at': None,
            }
        )
        if created:
            add_count += 1
    modeladmin.message_user(request, f'操作完成，本次新增 {add_count} 条黑名单记录')


@admin.action(description='解封选中用户（从平台黑名单移除）')
def unban_users(modeladmin, request, queryset):
    """
    批量解封用户
    找到对应用户邮箱的生效黑名单，标记为已解封，写入解封时间
    """
    count = 0
    for user in queryset:
        email_to_unban = user.real_email
        updated = BannedEmail.objects.filter(
            email=email_to_unban,
            is_active=True,
        ).update(is_active=False, unbanned_at=timezone.now())
        count += updated
    if count:
        modeladmin.message_user(request, f'已解封 {count} 个用户')
    else:
        modeladmin.message_user(request, '选中的用户中没有处于封禁状态的')


@admin.action(description='封禁选中租户（全员无法登录）')
def ban_tenants(modeladmin, request, queryset):
    """批量设置租户状态为banned封禁，租户下全部用户禁止登录"""
    count = queryset.update(status='banned')
    modeladmin.message_user(request, f'已成功封禁 {count} 个企业，该企业所有员工将无法登录。')


@admin.action(description='暂停选中租户（欠费/违规警告）')
def suspend_tenants(modeladmin, request, queryset):
    """批量设置租户状态为suspended暂停，用于欠费、临时违规场景"""
    count = queryset.update(status='suspended')
    modeladmin.message_user(request, f'已成功暂停 {count} 个企业服务。')


@admin.action(description='恢复选中租户（恢复正常使用）')
def activate_tenants(modeladmin, request, queryset):
    """批量恢复租户状态为active，恢复正常使用权限"""
    count = queryset.update(status='active')
    modeladmin.message_user(request, f'已成功恢复 {count} 个企业。')


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """租户后台管理配置"""
    # 列表展示字段
    list_display = ('id', 'name', 'status', 'created_at')
    # 筛选器
    list_filter = ('status',)
    # 搜索字段
    search_fields = ('name',)
    # 绑定批量操作
    actions = [ban_tenants, suspend_tenants, activate_tenants]

    def has_delete_permission(self, request, obj=None):
        """禁止后台直接删除租户，租户数据走业务流程软删除"""
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """审计日志后台管理配置，全程只读，不可增删改"""
    list_display = ('id', 'tenant', 'operator', 'target_user', 'action', 'created_at')
    list_filter = ('action', 'tenant')
    search_fields = ('operator__email', 'target_user__email', 'detail')
    readonly_fields = ('tenant', 'operator', 'target_user', 'action', 'detail', 'created_at')

    def has_add_permission(self, request):
        """禁止手动新增审计日志，日志由业务代码自动写入"""
        return False

    def has_change_permission(self, request, obj=None):
        """禁止修改审计日志，保证审计追溯可信"""
        return False

    def has_delete_permission(self, request, obj=None):
        """禁止删除审计日志，保证审计追溯可信"""
        return False


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """用户后台管理配置"""
    list_display = ('id', 'email', 'full_name', 'role', 'role_type', 'tenant', 'status', 'display_banned')
    list_filter = ('role', 'role_type', 'status')
    search_fields = ('email', 'full_name')
    actions = [ban_users, unban_users]

    def get_queryset(self, request):
        """
        查询集增强：使用子查询注解，标记用户是否被黑名单封禁
        同时校验原邮箱和当前邮箱两种黑名单匹配
        """
        qs = super().get_queryset(request)
        banned_by_email = BannedEmail.objects.filter(email=OuterRef('email'), is_active=True)
        banned_by_original = BannedEmail.objects.filter(email=OuterRef('original_email'), is_active=True)

        return qs.annotate(
            is_banned_annotated=Exists(banned_by_email) | Exists(banned_by_original)
        )

    def display_banned(self, obj):
        """自定义列：是否封禁，后台展示布尔图标"""
        return obj.is_banned_annotated

    display_banned.boolean = True
    display_banned.short_description = '已封禁'

    def get_actions(self, request):
        """移除原生批量删除按钮，用户不允许后台直接删除"""
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


@admin.register(BannedEmail)
class BannedEmailAdmin(admin.ModelAdmin):
    """黑名单邮箱后台管理配置"""
    list_display = ('id', 'email', 'is_active', 'banned_at', 'unbanned_at')
    list_filter = ('is_active',)
    search_fields = ('email',)
    actions = [unban_emails]
    readonly_fields = ('banned_at', 'unbanned_at')

    def get_actions(self, request):
        """移除原生批量删除按钮，黑名单记录保留审计"""
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions
