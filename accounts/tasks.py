import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail
from django.db.models.functions import Concat
from django.db.models import Value, F

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def clean_expired_invitations(self):
    """
    定时任务：清理已经过期的待激活邀请用户
    - 状态 pending、激活时间已过期
    - 把邮箱后缀加上 _expired_时间戳，避免后续重复邮箱冲突
    - 清空激活token、修改状态为expired
    - 失败最多自动重试3次，间隔10秒
    """
    try:
        # 懒导入，避免循环导入
        from .models import User
        now = timezone.now()
        timestamp = now.strftime('%Y%m%d%H%M%S')
        
        # 数据库批量更新（单条SQL，性能好，无N+1）
        count = User.objects.filter(
            status='pending',
            activation_token_expires_at__lt=now,
        ).update(
            # 原邮箱拼接过期标记，防止新建邀请时邮箱唯一冲突
            email=Concat(F('email'), Value(f'_expired_{timestamp}')),
            status='expired',
            activation_token='',
            activation_token_expires_at=None
        )
        logger.info(f"定时清理任务成功：清理了 {count} 个用户")
        return f'清理了 {count} 个过期待激活用户'

    except Exception as exc:
        logger.error(f"清理任务失败: {exc}")
        # 抛出异常 → Celery捕获，触发自动重试
        raise exc


@shared_task
def send_invite_email(email, inviter_name, company_name, token):
    """异步发送用户邀请激活邮件"""
    activate_url = f'http://127.0.0.1:8000/api/accounts/activate/page/?token={token}'

    send_mail(
        subject='【知识库系统】您已被邀请加入',
        message=f'{inviter_name} 邀请您加入 {company_name}，\n\n'
                f'请点击链接激活账号并设置密码：\n{activate_url}\n\n'
                f'链接 24 小时内有效，请及时激活。',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,  # 发送失败会抛出异常，便于celery记录
    )


@shared_task
def send_assign_role_email(email, full_name, old_role, new_role, company_name):
    """异步发送角色变更通知邮件"""
    send_mail(
        subject='【知识库系统】您的职位已更新',
        message=f'{full_name} 您好，\n\n'
                f'您在 {company_name} 的职位已由「{old_role}」变更为「{new_role}」。\n\n'
                f'请重新登录以获取最新权限。',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


@shared_task
def send_tenant_cancel_email(owners, company_name):
    """租户注销后，异步给所有负责人发送通知邮件"""
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
            # 单个邮箱发送失败，不中断剩下的邮件发送
            continue
