from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail
from .utils import expire_pending_user

# 清理过期的待激活用户
@shared_task
def clean_expired_invitations():
    from .models import User
    expired_users = User.objects.filter(
        status='pending',
        activation_token_expires_at__lt=timezone.now(),
    )
    count = 0
    for u in expired_users:
        expire_pending_user(u)
        count += 1
    return f'清理了 {count} 个过期待激活用户'

# 邀请用户后 发送邀请邮件
@shared_task
def send_invite_email(email, inviter_name, company_name, token):
    activate_url = f'http://127.0.0.1:8000/api/accounts/activate/page/?token={token}'

    send_mail(
        subject='【知识库系统】您已被邀请加入',
        message=f'{inviter_name} 邀请您加入 {company_name}，\n\n'
                f'请点击链接激活账号并设置密码：\n{activate_url}\n\n'
                f'链接 24 小时内有效，请及时激活。',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )

# 委任角色后 发送通知邮件
@shared_task
def send_assign_role_email(email, full_name, old_role, new_role, company_name):
    send_mail(
        subject='【知识库系统】您的职位已更新',
        message=f'{full_name} 您好，\n\n'
                f'您在 {company_name} 的职位已由「{old_role}」变更为「{new_role}」。\n\n'
                f'请重新登录以获取最新权限。',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )

# 注销公司后 给所有董事长发邮件
@shared_task
def send_tenant_cancel_email(owners, company_name):
    for owner in owners:
        try:
            send_mail(
                subject='【知识库系统】公司已注销',
                message=f'{owner["full_name"]} 您好，\n\n'
                        f'您所在的「{company_name}」已成功注销。\n\n',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner['email']],
                fail_silently=False,
            )
        except Exception:
            continue