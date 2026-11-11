import os
import logging
import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from celery import shared_task
from .models import Document, DocumentChunk
from .services.parser_factory import get_parser
from .services.cleaners import clean_markdown
from .services.splitters import split_into_chunks, tokenize_chunk
from .services.clients import get_milvus_client, embedding_client

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
        if doc.tenant.plan_type == 'pro':
            try:
                milvus_client = get_milvus_client()
                
                if milvus_client:
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
                    
                    # 清理同一版本链下历史旧版本的向量数据
                    root_id = doc.parent_doc_id or doc.id

                    # 查找该版本树下，除了当前 doc 以外的所有历史版本 ID
                    old_versions = Document.objects.filter(
                        Q(id=root_id) | Q(parent_doc_id=root_id),
                        tenant=doc.tenant
                    ).exclude(id=doc.id).values_list('id', flat=True)

                    old_doc_ids = list(old_versions)

                    if old_doc_ids:
                        # 使用 Milvus 的标量过滤删除旧向量
                        milvus_client.delete(
                            collection_name=collection_name,
                            filter=f"document_id in {old_doc_ids}"
                        )
                        logger.info(f"[{doc_id}] 已清理历史旧版本向量数据，旧文档 ID: {old_doc_ids}")

                    # 2. 提取新文本，调用智谱 API 向量化
                    texts = [chunk.content for chunk in chunk_objects]
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

                    # 3. 组装并插入新版本的向量到 Milvus
                    data_to_insert = []
                    for i, chunk in enumerate(chunk_objects):
                        data_to_insert.append({
                            "id": f"{doc.id}_{chunk.chunk_index}", 
                            "vector": all_vectors[i],
                            "text": chunk.content,
                            "document_id": doc.id
                        })
                    
                    milvus_client.insert(collection_name=collection_name, data=data_to_insert)
                    logger.info(f"[{doc_id}] 专业版租户：成功将 {len(texts)} 个向量写入 Milvus")
                else:
                    logger.warning(f"[{doc_id}] Milvus 客户端不可用，跳过向量化")
                
            except Exception as e:
                logger.error(f"[{doc_id}] 写入 Milvus 失败: {str(e)}", exc_info=True)
        else:
            # 轻量版跳过向量化
            logger.info(f"[{doc_id}] 轻量版租户：跳过向量化入库，仅保留 MySQL 全文索引")

        # 通知 FastAPI 清理缓存
        try:
            if doc.tenant.plan_type == 'pro':
                fastapi_url = os.getenv("FASTAPI_URL", "http://fastapi-app:9000")
                refresh_url = f"{fastapi_url}/api/refresh_cache?tenant_id={doc.tenant_id}"
                requests.post(refresh_url, timeout=3)
                logger.info(f"[{doc_id}] 已通知 FastAPI 清理 BM25 缓存")
        except Exception as e:
            logger.warning(f"[{doc_id}] 通知 FastAPI 清理缓存失败: {e}")

        return f'解析成功，共 {len(chunks)} 个块'
    
    # 解析失败 
    except Exception as e:
        logger.error(f"文档解析失败 ID:{doc_id} - {str(e)}", exc_info=True)
        doc.status = 'failed'
        doc.error_message = str(e)[:500]
        doc.save(update_fields=['status', 'error_message'])
        return f'解析失败: {str(e)}'