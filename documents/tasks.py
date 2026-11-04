import os
import logging
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from celery import shared_task
from .models import Document, DocumentChunk
from .services.parser_factory import get_parser
from .services.cleaners import clean_markdown
from .services.splitters import split_into_chunks, tokenize_chunk
from .services.clients import milvus_client, embedding_client

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
        
        # 获取后缀
        suffix = os.path.splitext(full_path)[1].lower()

        parser = get_parser(suffix)
        raw_text = parser.parse(full_path)
        text = clean_markdown(raw_text)

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
            # 先删除该文档已经存在的旧分片
            DocumentChunk.objects.filter(document=doc).delete()

            # 批量插入
            DocumentChunk.objects.bulk_create(chunk_objects)

            # 更新状态为成功
            doc.status = 'parsed'
            doc.parsed_at = timezone.now()
            doc.save(update_fields=['status', 'parsed_at'])

        # 向量化并写入 Milvus
        try:
            collection_name = f"tenant_{doc.tenant_id}"
            
            # 1. 检查并创建集合
            if collection_name not in milvus_client.list_collections():
                milvus_client.create_collection(
                    collection_name=collection_name,
                    dimension=1024,
                    metric_type="IP",
                    id_type="string",
                    max_length=65535
                )
                logger.info(f"新建 Milvus 集合: {collection_name}")

            # 2. 提取文本，调用智谱 API 向量化
            texts = [chunk.content for chunk in chunk_objects]
            
            # 批量请求，避免单次请求过长
            all_vectors = []
            batch_size = 20
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i+batch_size]
                response = embedding_client.embeddings.create(
                    model="embedding-3",
                    input=batch_texts,
                    dimensions=1024
                )
                batch_vectors = [data.embedding for data in response.data]
                all_vectors.extend(batch_vectors)

            # 3. 组装并插入 Milvus
            data_to_insert = []
            for i, chunk in enumerate(chunk_objects):
                data_to_insert.append({
                    "id": f"{doc.id}_{chunk.chunk_index}", 
                    "vector": all_vectors[i],
                    "text": chunk.content,
                    "document_id": doc.id
                })
            
            milvus_client.insert(collection_name=collection_name, data=data_to_insert)
            logger.info(f"[{doc_id}] 成功将 {len(texts)} 个向量写入 Milvus 集合: {collection_name}")
            
        except Exception as e:
            # 向量化失败不影响主流程（MySQL 已经有数据了），仅记录日志供后续重试
            logger.error(f"[{doc_id}] 写入 Milvus 失败: {str(e)}", exc_info=True)

        return f'解析成功，共 {len(chunks)} 个块'
    
    # 解析失败 
    except Exception as e:
        logger.error(f"文档解析失败 ID:{doc_id} - {str(e)}", exc_info=True)
        doc.status = 'failed'
        doc.error_message = str(e)[:500]
        doc.save(update_fields=['status', 'error_message'])
        return f'解析失败: {str(e)}'