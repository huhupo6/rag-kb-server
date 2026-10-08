from django.contrib import admin
from django.db.models import Exists, OuterRef
from django.utils import timezone
from .models import Tenant, User, BannedEmail, AuditLog

# 封禁用户：把他们的邮箱加入 BannedEmail 平台黑名单
@admin.action(description='封禁选中用户（加入平台黑名单）')
def ban_users(modeladmin, request, queryset):
    add_count = 0
    for user in queryset:
        email_to_ban = user.original_email or user.email
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

# 解封用户：把他们的邮箱加入 BannedEmail 平台黑名单中软删
@admin.action(description='解封选中邮箱')
def unban_emails(modeladmin, request, queryset):
    active_qs = queryset.filter(is_active=True)
    emails = list(active_qs.values_list('email', flat=True))
    count = active_qs.update(is_active=False, unbanned_at=timezone.now())
    if count:
        modeladmin.message_user(request, f'已解封 {count} 个邮箱：{", ".join(emails)}')
    else:
        modeladmin.message_user(request, '选中的记录中没有生效中的封禁')

# 审计日志表
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'operator', 'target_user', 'action', 'created_at')
    list_filter = ('action', 'tenant')
    search_fields = ('operator__email', 'target_user__email', 'detail')
    readonly_fields = ('tenant', 'operator', 'target_user', 'action', 'detail', 'created_at')
    
    # 审计日志不允许手动新增
    def has_add_permission(self, request):
        return False

    # 审计日志不允许修改
    def has_change_permission(self, request, obj=None):
        return False

    # 审计日志不允许删除
    def has_delete_permission(self, request, obj=None):
        return False
    
# 租户表
@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('name',)

# 用户表
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    # 后台展示字段
    list_display = ('id', 'email', 'full_name', 'role', 'role_type', 'tenant', 'status', 'display_banned')
    # 后台筛选字段
    list_filter = ('role', 'role_type', 'status')
    # 后台可搜索字段
    search_fields = ('email', 'full_name')
    actions = [ban_users]

    # 在列表页显示用户是否被封禁
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        banned_by_email = BannedEmail.objects.filter(email=OuterRef('email'), is_active=True)
        banned_by_original = BannedEmail.objects.filter(email=OuterRef('original_email'), is_active=True)

        return qs.annotate(
            is_banned_annotated=Exists(banned_by_email) | Exists(banned_by_original)
        )

    def display_banned(self, obj):
        return obj.is_banned_annotated
    display_banned.boolean = True
    display_banned.short_description = '已封禁'

# 封禁邮箱表
@admin.register(BannedEmail)
class BannedEmailAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'is_active', 'banned_at', 'unbanned_at')
    list_filter = ('is_active',)
    search_fields = ('email',)
    actions = [unban_emails]