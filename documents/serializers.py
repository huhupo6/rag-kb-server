import os
import uuid
import logging

from django.conf import settings
from django.db import transaction

from rest_framework import serializers

from .models import Document
from .tasks import parse_document

logger = logging.getLogger(__name__)

# 安全保存文件
def save_uploaded_file(file_obj, doc_id):
    # 1. 提取纯文件名，过滤掉 '../' 等恶意路径字符
    safe_name = os.path.basename(file_obj.name)

    # 2. 拼接相对路径 (加入 doc_id 防止同名覆盖)
    relative_path = os.path.join('documents', f'{doc_id}_{safe_name}')
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)

    # 3. 创建目录并分块写入磁盘
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'wb+') as f:
        for chunk in file_obj.chunks():
            f.write(chunk)
            
    return relative_path, full_path

# 上传文档 序列化器
class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField(label='文件')
    category = serializers.ChoiceField(
        choices=Document.CATEGORY_CHOICES,
        label='文档分类'
    )

    def validate_file(self, value):
        # 1. 检查扩展名
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')

        # 2. 检查大小
        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')

        return value

    def create(self, validated_data):
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
                
                # 调用公共方法存文件
                relative_path, full_path = save_uploaded_file(validated_data['file'], doc.id)

                doc.file_path = relative_path
                doc.save(update_fields=['file_path'])

                transaction.on_commit(lambda: parse_document.delay(doc.id))

            return doc
        except Exception as e:
            # 清理孤儿文件
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e


# 新版本文档上传 序列化器
class DocumentNewVersionSerializer(serializers.Serializer):
    file = serializers.FileField(label='新版本文件')

    def validate_file(self, value):
        # 1. 检查拓展名
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')

        # 2. 检查大小
        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')
        return value

    def create(self, validated_data):
        request = self.context['request']
        user = request.user
        old_doc = self.context['old_doc']
        full_path = None

        try:
            with transaction.atomic():
                # 1. 旧版本退役
                old_doc.is_current = False
                old_doc.save(update_fields=['is_current'])

                safe_name = os.path.basename(validated_data['file'].name)

                # 2. 创建新版本记录
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

                # 3. 调用公共方法存文件
                relative_path, full_path = save_uploaded_file(validated_data['file'], new_doc.id)

                new_doc.file_path = relative_path
                new_doc.save(update_fields=['file_path'])

                transaction.on_commit(lambda: parse_document.delay(new_doc.id))

            return new_doc
        except Exception as e:
            # 清理孤儿文件
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e
       