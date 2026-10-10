import redis
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

# 全局 Redis 客户端
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)

# 校验用户所属租户是否处于正常状态。
def check_tenant_active(user):
    if user.tenant and user.tenant.status != 'active':
        status_messages = {
            'suspended': '所属公司服务已暂停，请联系企业管理员处理',
            'banned': '所属公司已被封禁，请联系平台客服',
            'cancelled': '所属公司已注销',
        }
        raise PermissionDenied(
            status_messages.get(user.tenant.status, '所属公司状态异常，请联系平台客服')
        )

# 把待激活用户标记为 邀请过期，释放邮箱，清空令牌
def expire_pending_user(user):
    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    user.email = f'{user.email}_expired_{timestamp}'
    user.status = 'expired'
    user.activation_token = ''
    user.activation_token_expires_at = None
    user.save(update_fields=[
        'email', 'status', 'activation_token', 'activation_token_expires_at'
    ])