import os
import logging

from django.conf import settings
from django.db import transaction

from rest_framework import serializers

from .models import Document
from .tasks import parse_document

logger = logging.getLogger(__name__)


def save_uploaded_file(file_obj, doc_id):
    """
    安全保存上传文件工具函数
    - 使用 basename 剥离危险路径前缀，防御路径穿越
    - 使用文档 ID 嵌入文件名，避免同名文件互相覆盖
    - 自动创建存储目录，分块写入大文件，减少内存压力
    :param file_obj: DRF 上传文件对象
    :param doc_id: 文档主键，用于生成唯一文件名
    :return: relative_path(相对存储路径), full_path(磁盘绝对路径)
    """
    # 提取纯净文件名，过滤 ../ 这类恶意路径
    safe_name = os.path.basename(file_obj.name)

    # 拼接相对路径，带上 doc_id 防止同名覆盖
    relative_path = os.path.join('documents', f'{doc_id}_{safe_name}')
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)

    # 如果目录不存在则自动创建
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    # 分块写入磁盘，用于文件上传
    with open(full_path, 'wb+') as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

    return relative_path, full_path


class DocumentUploadSerializer(serializers.Serializer):
    """
    全新文档上传序列化器
    用于租户上传一份全新文档，包含文件校验、套餐配额校验、入库、落盘、异步解析
    """
    file = serializers.FileField(label='文件')
    category = serializers.ChoiceField(
        choices=Document.CATEGORY_CHOICES,
        label='文档分类'
    )

    def validate_file(self, value):
        """
        文件单字段校验
        - 文件后缀白名单：pdf / docx / txt
        - 文件大小上限：50MB
        """
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')

        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')

        return value

    def validate(self, attrs):
        """
        整体业务校验：租户文档配额控制
        - 轻享版 free：最多 10 份有效文档
        - 专业版 pro：最多 500 份有效文档
        - 统计范围：当前版本、未软删除文档
        """
        user = self.context['request'].user
        tenant = user.tenant
        max_docs = 10 if tenant.plan_type == 'free' else 500

        current_docs_count = Document.objects.filter(
            tenant=tenant,
            is_current=True,
            is_deleted=False
        ).count()

        if current_docs_count >= max_docs:
            plan_name = "轻享版" if tenant.plan_type == 'free' else "专业版"
            raise serializers.ValidationError(
                f'当前【{plan_name}】最多只能上传 {max_docs} 个文档。'
                f'如需上传更多，请删除旧文档或联系管理员升级套餐。'
            )

        return attrs

    def create(self, validated_data):
        """
        创建全新文档主流程
        1. 在数据库事务内新建文档记录，状态 pending（等待解析）
        2. 将文件持久化到磁盘，回填文件路径
        3. 使用 transaction.on_commit，事务提交成功后才触发 Celery 异步解析任务
        4. 如果抛出异常，删除已写入磁盘的文件，防止孤儿脏文件
        """
        request = self.context['request']
        user = request.user
        full_path = None

        try:
            with transaction.atomic():
                safe_name = os.path.basename(validated_data['file'].name)

                doc = Document.objects.create(
                    tenant=user.tenant,
                    category=validated_data['category'],
                    file_name=safe_name,
                    file_path='',
                    version=1,
                    parent_doc_id=None,
                    is_current=True,
                    status='pending',
                    uploaded_by=user,
                )

                relative_path, full_path = save_uploaded_file(validated_data['file'], doc.id)
                doc.file_path = relative_path
                doc.save(update_fields=['file_path'])

                # 事务提交之后再启动解析，避免回滚后出现无效任务
                transaction.on_commit(lambda: parse_document.delay(doc.id))

                return doc
        except Exception as e:
            # 事务回滚，清理已经落地的文件
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e


class DocumentNewVersionSerializer(serializers.Serializer):
    """
    文档新版本上传序列化器
    用于对已有文档上传新版本，维护文档版本链
    逻辑：旧文档标记为非当前版本，新建一条版本+1 的记录作为当前生效文档
    """
    file = serializers.FileField(label='新版本文件')

    def validate_file(self, value):
        """
        新版本文件校验，和新建文档保持一致：后缀 + 文件大小
        """
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')

        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')
        return value

    def create(self, validated_data):
        """
        创建新版本文档流程
        1. 将原有文档 is_current 置为 False，不再作为当前生效版本
        2. 创建新版本记录，版本号自增，挂载到同一个文档版本链
        3. 文件落盘，提交事务后异步调用解析任务
        4. 异常时清理磁盘上的孤儿文件
        """
        request = self.context['request']
        user = request.user
        old_doc = self.context['old_doc']
        full_path = None

        try:
            with transaction.atomic():
                # 旧版本下线
                old_doc.is_current = False
                old_doc.save(update_fields=['is_current'])

                safe_name = os.path.basename(validated_data['file'].name)

                # 创建新版本记录，继承分类、归属，版本号 +1
                new_doc = Document.objects.create(
                    tenant=user.tenant,
                    category=old_doc.category,
                    file_name=safe_name,
                    file_path='',
                    version=old_doc.version + 1,
                    parent_doc_id=old_doc.parent_doc_id or old_doc.id,
                    is_current=True,
                    status='pending',
                    uploaded_by=user,
                )

                relative_path, full_path = save_uploaded_file(validated_data['file'], new_doc.id)
                new_doc.file_path = relative_path
                new_doc.save(update_fields=['file_path'])

                transaction.on_commit(lambda: parse_document.delay(new_doc.id))

                return new_doc
        except Exception as e:
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e
