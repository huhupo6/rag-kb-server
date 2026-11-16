from rest_framework.exceptions import PermissionDenied


def check_tenant_active(user):
    """
    租户状态校验工具函数
    作用：校验当前用户所属租户是否处于正常可用状态
    校验逻辑：
        1. 判断租户存在，并且状态不是 active（正常激活）
        2. 对暂停、封禁、注销等状态返回对应提示文案
        3. 状态异常时抛出 DRF 的 PermissionDenied 权限异常，接口会自动返回403
    """
    if user.tenant and user.tenant.status != 'active':
        # 不同租户状态对应的提示文案映射
        status_messages = {
            'suspended': '所属公司服务已暂停，请联系企业管理员处理',
            'banned': '所属公司已被封禁，请联系平台客服',
            'cancelled': '所属公司已注销',
        }
        raise PermissionDenied(
            status_messages.get(user.tenant.status, '所属公司状态异常，请联系平台客服')
        )
