from django.db import models

# 文档表
class Document(models.Model):
    CATEGORY_CHOICES = [
        ('attendance', '考勤制度'),
        ('salary', '薪酬福利'),
        ('performance', '绩效考核'),
        ('contract', '合同协议'),
        ('compliance', '合规政策'),
        ('process', '审批流程'),
        ('training', '培训资料'),
    ]
    STATUS_CHOICES = [
        ('pending', '待解析'),
        ('parsing', '解析中'),
        ('parsed', '解析成功'),
        ('failed', '解析失败'),
    ]

    # 所属
    tenant = models.ForeignKey('accounts.Tenant', on_delete=models.PROTECT, verbose_name='所属公司')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name='文档分类')
    
    # 文件信息
    file_name = models.CharField(max_length=255, verbose_name='文件名')
    file_path = models.CharField(max_length=500, verbose_name='文件存储路径')
    
    # 版本管理
    version = models.IntegerField(default=1, verbose_name='版本号')
    parent_doc_id = models.IntegerField(null=True, blank=True, verbose_name='根文档ID')
    is_current = models.BooleanField(default=True, verbose_name='是否为当前最新版本')
    
    # 状态
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='解析状态')
    error_message = models.TextField(blank=True, verbose_name='解析失败原因')
    
    # 软删除
    is_deleted = models.BooleanField(default=False, verbose_name='是否已删除')

    # 操作者与时间
    uploaded_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='上传者')
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='上传时间')
    parsed_at = models.DateTimeField(null=True, blank=True, verbose_name='解析完成时间')

    class Meta:
        db_table = 'documents'
        verbose_name = '文档'
        verbose_name_plural = '文档'
        ordering = ['-uploaded_at']
        indexes = [
            # 文档列表查询
            models.Index(fields=['tenant', 'is_current', 'is_deleted', 'uploaded_at'],name='doc_list_idx'),
            # 文档历史版本查询
            models.Index(fields=['tenant', 'parent_doc_id', 'is_deleted', 'version'], name='doc_version_idx'),
        ]

    def __str__(self):
        return f'{self.file_name}（V{self.version}）'

# 文档块表
class DocumentChunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, verbose_name='所属文档')
    content = models.TextField(verbose_name='原文内容')
    content_tokenized = models.TextField(verbose_name='jieba 分词后的文本')
    chunk_index = models.IntegerField(verbose_name='块序号')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'document_chunks'
        verbose_name = '文档块'
        verbose_name_plural = '文档块'
        ordering = ['document', 'chunk_index']

        # content_tokenized 字段的 FULLTEXT 全文索引已通过 0002 migration 使用 RunSQL 创建

    def __str__(self):
        return f'{self.document.file_name} - Chunk {self.chunk_index}'