from django.db import models

# 文档表
class Document(models.Model):
    """
    文档主表，支持文档版本链管理、软删除、解析状态追踪、租户隔离
    用于企业合同/制度类文档上传、版本迭代、解析任务管理
    """
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

    # 租户隔离
    tenant = models.ForeignKey('accounts.Tenant', on_delete=models.PROTECT, verbose_name='所属公司')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name='文档分类')

    # 文件存储信息
    file_name = models.CharField(max_length=255, verbose_name='文件名')
    file_path = models.CharField(max_length=500, verbose_name='文件存储路径')

    # 文件SHA256哈希值，用于秒传与去重
    file_hash = models.CharField(
        max_length=64, 
        blank=True, 
        default='', 
        verbose_name='文件SHA256哈希'
    )

    # 版本链核心字段
    version = models.IntegerField(default=1, verbose_name='版本号')
    parent_doc_id = models.IntegerField(null=True, blank=True, verbose_name='根文档ID')
    is_current = models.BooleanField(default=True, verbose_name='是否为当前最新版本')

    # 解析任务状态（MinerU解析文档、切分chunk使用）
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='解析状态')
    error_message = models.TextField(blank=True, verbose_name='解析失败原因')

    # 软删除
    is_deleted = models.BooleanField(default=False, verbose_name='是否已删除')

    # 操作人、时间记录
    uploaded_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='上传者')
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='上传时间')
    parsed_at = models.DateTimeField(null=True, blank=True, verbose_name='解析完成时间')

    class Meta:
        db_table = 'documents'
        verbose_name = '文档'
        verbose_name_plural = '文档'
        ordering = ['-uploaded_at']
        indexes = [
            # 租户文档列表查询：租户+是否最新+是否删除+上传时间
            models.Index(fields=['tenant', 'is_current', 'is_deleted', 'uploaded_at'], name='doc_list_idx'),
            # 同一个根文档的全部历史版本查询
            models.Index(fields=['tenant', 'parent_doc_id', 'is_deleted', 'version'], name='doc_version_idx'),
            # 秒传去重联合索引
            models.Index(fields=['tenant', 'file_hash'], name='doc_hash_idx'),
        ]

    def __str__(self):
        return f'{self.file_name}（V{self.version}）'


# 文档块表
class DocumentChunk(models.Model):
    """
    文档切片表，MinerU解析文档后，按策略切分得到chunk
    content_tokenized 存储 jieba分词+数字单位保护后的文本，供Free版MySQL全文检索使用
    Pro版会同步把chunk向量化写入Milvus向量库
    """
    document = models.ForeignKey(Document, on_delete=models.CASCADE, verbose_name='所属文档')
    content = models.TextField(verbose_name='原文内容')
    content_tokenized = models.TextField(verbose_name='jieba 分词后的文本，用于MySQL全文检索')
    chunk_index = models.IntegerField(verbose_name='块序号，记录在文档内顺序')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'document_chunks'
        verbose_name = '文档块'
        verbose_name_plural = '文档块'
        ordering = ['document', 'chunk_index']

        # 说明：content_tokenized 的 FULLTEXT 全文索引，通过迁移脚本RunSQL创建
        # migrations 里单独执行SQL: ALTER TABLE document_chunks ADD FULLTEXT INDEX ft_content_tokenized(content_tokenized)

    def __str__(self):
        return f'{self.document.file_name} - Chunk {self.chunk_index}'
