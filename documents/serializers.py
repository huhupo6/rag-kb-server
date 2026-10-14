import os
from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from .models import Document
from .tasks import parse_document

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

        with transaction.atomic():
            doc = Document.objects.create(
                tenant=user.tenant,
                category=validated_data['category'],
                file_name=validated_data['file'].name,
                file_path='',
                version=1,
                parent_doc_id=None,
                is_current=True,
                status='pending',
                uploaded_by=user,
            )
            # 生成相对路径
            relative_path = os.path.join('documents', f'{doc.id}_{validated_data["file"].name}')
            # 拼接磁盘绝对路径
            full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
            # 创建文件夹
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # 循环分块写入文件
            with open(full_path, 'wb+') as f:
                for chunk in validated_data['file'].chunks():
                    f.write(chunk)

            doc.file_path = relative_path
            doc.save(update_fields=['file_path'])

            transaction.on_commit(lambda: parse_document.delay(doc.id))

        return doc

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

        with transaction.atomic():
            # 更改旧文档为旧版本
            old_doc.is_current = False
            old_doc.save(update_fields=['is_current'])

            # 创建新版本记录
            new_doc = Document.objects.create(
                tenant = user.tenant,
                category=old_doc.category,
                file_name=validated_data['file'].name,
                file_path='',
                version=old_doc.version + 1,
                parent_doc_id=old_doc.parent_doc_id or old_doc.id,
                is_current=True,
                status='pending',
                uploaded_by=user,
            )

            # 保存文件
            relative_path = os.path.join('documents', f'{new_doc.id}_{validated_data["file"].name}')
            full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, 'wb+') as f:
                for chunk in validated_data['file'].chunks():
                    f.write(chunk)

            # 更新 file_path
            new_doc.file_path = relative_path
            new_doc.save(update_fields=['file_path'])

            transaction.on_commit(lambda: parse_document.delay(new_doc.id))

        return new_doc

