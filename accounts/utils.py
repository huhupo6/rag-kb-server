import redis
from django.conf import settings
from rest_framework.exceptions import PermissionDenied

# 全局 Redis 客户端
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)

'''
校验用户所属租户是否处于正常状态。
被封禁/暂停/注销的租户下，所有业务操作都应该被拦截。
可在视图入口统一调用。
'''
def check_tenant_active(user):
    if user.tenant and user.tenant.status != 'active':
        status_messages = {
            'suspended': '所属公司服务已暂停，请联系管理员续费',
            'banned': '所属公司已被封禁，请联系平台管理员',
            'cancelled': '所属公司已注销',
        }
        raise PermissionDenied(
            status_messages.get(user.tenant.status, '所属公司状态异常')
        )