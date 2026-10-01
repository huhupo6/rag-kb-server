from django.contrib import admin
from .models import Tenant, User

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    # 后台展示字段
    list_display = ('id', 'name', 'is_active', 'created_at')

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    # 后台展示字段
    list_display = ('id', 'email', 'full_name', 'role', 'role_type', 'tenant', 'status')
    # 后台筛选字段
    list_filter = ('role', 'role_type', 'status')
    # 后台可搜索字段
    search_fields = ('email', 'full_name')