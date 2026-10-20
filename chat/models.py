from django.db import models

# 问答历史记录表
class ChatHistory(models.Model):

    tenant = models.ForeignKey('accounts.Tenant', on_delete=models.CASCADE, verbose_name='所属公司')
    user = models.ForeignKey(
        'accounts.User', 
        on_delete=models.PROTECT, 
        verbose_name='提问者'
    )
    
    question = models.TextField(verbose_name='用户问题')
    answer = models.TextField(verbose_name='AI 答案')

    cited_chunks = models.JSONField(default=list, blank=True, verbose_name='引用的文档块ID列表')

    is_sensitive = models.BooleanField(default=False, verbose_name='是否敏感问题')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='提问时间')

    class Meta:
        db_table = 'chat_history'
        verbose_name = '问答历史'
        verbose_name_plural = '问答历史'
        ordering = ['-created_at']

        indexes = [
            # HR、董事查询全公司日志索引
            models.Index(fields=['tenant', 'created_at'], name='chat_logs_idx'),
            # 个人历史专用的联合索引
            models.Index(fields=['user', 'created_at'], name='my_history_idx'),
        ]

    def __str__(self):
        return f'{self.user.full_name if self.user else "未知"} - {self.question[:20]}'