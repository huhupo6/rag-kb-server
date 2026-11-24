# 1. Python 内置
import os
import logging

# 2. 第三方库
import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

# 3. Django
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

# 4. 当前app自定义模块
from .models import Document, DocumentChunk
from .services.parser_factory import get_parser
from .services.cleaners import clean_markdown
from .services.splitters import split_into_chunks, tokenize_chunk
from .services.clients import get_milvus_client, embedding_client

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def parse_document(self, doc_id):
    """
    Celery异步任务：文档解析、文本清洗、分块、分片入库、向量化（Pro套餐）
    执行流程：
        1. 获取文档记录，状态改为 parsing（解析中）
        2. MinerU解析文件 → Markdown清洗 → 带跨页表格保护策略切分chunk
        3. 事务内：删除该文档旧分片，批量写入新DocumentChunk
        4. 套餐分支：
            - free：仅写入MySQL，content_tokenized用于MySQL全文检索，跳过向量化
            - pro：额外调用Embedding生成向量写入Milvus，自动清理版本链旧向量，通知FastAPI刷新BM25缓存
    状态流转：pending → parsing → parsed / failed
    """
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return f'Document {doc_id} 不存在'

    # 修改文档状态：解析中
    doc.status = 'parsing'
    doc.save(update_fields=['status'])

    try:
        # 拼接文件完整物理路径
        full_path = os.path.join(settings.MEDIA_ROOT, doc.file_path)
        # 获取文件后缀，统一小写
        suffix = os.path.splitext(full_path)[1].lower()

        # 按文件类型获取解析器（底层MinerU，支持PDF/DOCX跨页表格识别合并）
        parser = get_parser(suffix)
        raw_text = parser.parse(full_path)

        # 文本大小熔断：假设超过 100 万字符（约 2MB 纯文本），直接判定失败
        if len(raw_text) > 1_000_000:
            raise ValueError("解析文本内容过大，超出系统处理上限，请拆分文档后上传")

        # 清洗Markdown多余标签、脏格式
        text = clean_markdown(raw_text)

        # 文档分块：splitters，带表格整体保护，避免跨页表格被切碎
        chunks = split_into_chunks(text)
        if not chunks:
            raise ValueError("文档内容为空，未提取到任何有效文本")

        chunk_objects = []
        for idx, chunk in enumerate(chunks):
            chunk_objects.append(
                DocumentChunk(
                    document=doc,
                    content=chunk,
                    # 分词：和检索查询侧共用同一套tokenize函数，解决分词不一致召回失败bug
                    content_tokenized=tokenize_chunk(chunk),
                    chunk_index=idx,
                )
            )

        # 数据库事务：保证分片原子更新，要么全部更新成功，要么回滚
        with transaction.atomic():
            # 删除当前文档历史旧分片
            DocumentChunk.objects.filter(document=doc).delete()
            # 批量插入新分片，减少IO
            DocumentChunk.objects.bulk_create(chunk_objects)
            # 更新文档状态为解析成功，记录解析完成时间
            doc.status = 'parsed'
            doc.parsed_at = timezone.now()
            doc.save(update_fields=['status', 'parsed_at'])

        # ========== Pro套餐专属逻辑：向量化写入Milvus向量库 ==========
        if doc.tenant.plan_type == 'pro':
            try:
                milvus_client = get_milvus_client()
                if not milvus_client:
                    logger.warning(f"[{doc_id}] Milvus 客户端不可用，跳过向量化")
                else:
                    # 每个租户独立Milvus集合，实现租户数据隔离
                    collection_name = f"tenant_{doc.tenant_id}"
                    # 集合不存在则新建，向量维度1024，内积IP相似度
                    if collection_name not in milvus_client.list_collections():
                        milvus_client.create_collection(
                            collection_name=collection_name,
                            dimension=1024,
                            metric_type="IP",
                            id_type="string",
                            max_length=65535
                        )
                        logger.info(f"新建 Milvus 集合: {collection_name}")

                    # 版本链清理：找到根文档，删除同版本链下旧版本的向量，只保留最新版向量
                    root_id = doc.parent_doc_id or doc.id
                    old_versions = Document.objects.filter(
                        Q(id=root_id) | Q(parent_doc_id=root_id),
                        tenant=doc.tenant
                    ).exclude(id=doc.id).values_list('id', flat=True)
                    old_doc_ids = list(old_versions)

                    if old_doc_ids:
                        milvus_client.delete(
                            collection_name=collection_name,
                            filter=f"document_id in {old_doc_ids}"
                        )
                        logger.info(f"[{doc_id}] 已清理历史旧版本向量数据，旧文档 ID: {old_doc_ids}")

                    # 批量调用智谱Embedding接口，生成向量，分批调用避免超限
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

                    # 组装向量数据写入Milvus
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
            except Exception as e:
                logger.error(f"[{doc_id}] 写入 Milvus 失败: {str(e)}", exc_info=True)
        else:
            # Free轻量版：不需要向量，只依赖MySQL全文检索
            logger.info(f"[{doc_id}] 轻量版租户：跳过向量化入库，仅保留 MySQL 全文索引")

        # Pro套餐：通知FastAPI检索微服务，清空该租户BM25内存缓存，防止读到过期分片
        if doc.tenant.plan_type == 'pro':
            try:
                fastapi_url = os.getenv("FASTAPI_URL", "http://fastapi-app:9000")
                refresh_url = f"{fastapi_url}/api/refresh_cache?tenant_id={doc.tenant_id}"
                requests.post(refresh_url, timeout=3)
                logger.info(f"[{doc_id}] 已通知 FastAPI 清理 BM25 缓存")
            except Exception as e:
                logger.warning(f"[{doc_id}] 通知 FastAPI 清理缓存失败: {e}")

        return f'解析成功，共 {len(chunks)} 个块'

    except requests.exceptions.RequestException as exc:
        # MinerU 微服务抖动或超时，触发 Celery 自动重试
        logger.warning(f"文档 {doc_id} 解析遇到网络异常，准备重试: {exc}")
        raise self.retry(exc=exc)

    # 捕获 Celery 软超时（防止 MinerU 卡死导致 Worker 长期被占用）
    except SoftTimeLimitExceeded:
        logger.error(f"文档 {doc_id} 解析超时（SoftTimeLimitExceeded），已被强制中断")
        doc.status = 'failed'
        doc.error_message = "解析超时：文件过大或解析服务卡死，请重试或联系管理员"
        doc.save(update_fields=['status', 'error_message'])

        return f'解析超时: {doc_id}'

    except Exception as exc:
        # 全局异常捕获：标记文档解析失败，记录错误信息
        logger.error(f"文档 {doc_id} 解析彻底失败: {exc}", exc_info=True)
        doc.status = 'failed'

        # 判断是否是因为重试耗尽导致的失败
        if isinstance(exc, MaxRetriesExceededError):
            doc.error_message = "解析失败：MinerU 微服务网络持续异常，请稍后重新上传"
        else:
            doc.error_message = str(exc)[:500]
        
        doc.save(update_fields=['status', 'error_message'])
        # 抛出异常，触发 RabbitMQ 的死信机制 (x-dead-letter-exchange)
        raise exc