from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

# 邀请用户后发送邀请邮件


# 委任角色后发送通知邮件
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

