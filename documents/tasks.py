import os
import logging
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from celery import shared_task
from .models import Document, DocumentChunk
from .services import extract_text, split_into_chunks, tokenize_chunk

logger = logging.getLogger(__name__)

# 异步解析文档
@shared_task
def parse_document(doc_id):
    # 尝试拿到原文件
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return f'Document {doc_id} 不存在'

    # 拿到文件状态改为解析中
    doc.status = 'parsing'
    doc.save(update_fields=['status'])

    try:
        full_path = os.path.join(settings.MEDIA_ROOT, doc.file_path)
        
        # 抽文本
        text = extract_text(full_path)

        # 分块
        chunks = split_into_chunks(text)

        if not chunks:
            raise ValueError("文档内容为空，未提取到任何有效文本")

        chunk_objects = []
        # 逐块分词 + 写入
        for idx, chunk in enumerate(chunks):
            chunk_objects.append(
                DocumentChunk(
                    document=doc,
                    content=chunk,
                    content_tokenized=tokenize_chunk(chunk),
                    chunk_index=idx,
                )
            )

        with transaction.atomic():
            # 重试机制，先删除可能存在的旧块
            DocumentChunk.objects.filter(document=doc).delete()

            # 一次性插入
            DocumentChunk.objects.bulk_create(chunk_objects)

            # 更新状态为成功
            doc.status = 'parsed'
            doc.parsed_at = timezone.now()
            doc.save(update_fields=['status', 'parsed_at'])

        return f'解析成功，共 {len(chunks)} 个块'
    
    # 解析失败 
    except Exception as e:
        logger.error(f"文档解析失败 ID:{doc_id} - {str(e)}")
        doc.status = 'failed'
        doc.error_message = str(e)
        doc.save(update_fields=['status', 'error_message'])
        return f'解析失败: {str(e)}'