# 标准库
import os
import logging

# 第三方库
import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response

# Django 内置
from django.db import transaction
from django.db.models import Q

# 项目内部模块
from common.permissions import check_tenant_active
from .models import Document
from .serializers import DocumentUploadSerializer, DocumentNewVersionSerializer
from .services.clients import get_milvus_client

# 初始化本模块日志器
logger = logging.getLogger(__name__)


@api_view(['POST'])
def upload_document(request):
    """
    文档上传接口
    功能：
        1. 校验租户状态，判断租户是否正常可用
        2. 权限校验：仅 owner / admin 角色允许上传文档
        3. 序列化器校验文件后缀、大小、参数合法性，并持久化文档记录
        4. 文档原始文件入库后，文档解析、文本切块、向量化交给 Celery 异步任务后台执行
    返回：文档基础信息，提示后台正在解析
    """
    # 校验租户状态，租户停用直接抛出异常拦截
    check_tenant_active(request.user)

    # 上传权限校验：只允许租户owner、admin上传文档
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限上传文档'}, status=403)

    # 序列化器校验文件与入参，传入request上下文用于租户、用户注入
    serializer = DocumentUploadSerializer(
        data=request.data,
        context={'request': request}
    )
    if serializer.is_valid():
        # 保存文档记录，序列化器内部会触发异步解析任务
        doc = serializer.save()
        return Response({
            'message': '上传成功，正在后台解析',
            'document': {
                'id': doc.id,
                'file_name': doc.file_name,
                'category': doc.category,
                'status': doc.status,
            }
        }, status=201)
    # 校验不通过，返回序列化器校验错误信息
    return Response(serializer.errors, status=400)


@api_view(['GET'])
def list_documents(request):
    """
    文档列表查询接口
    功能：查询当前租户下所有【当前生效版本、未软删除】的文档，按上传时间倒序返回
    """
    # 校验租户状态
    check_tenant_active(request.user)
    # 过滤条件：归属当前租户、是当前版本、未软删除
    docs = Document.objects.filter(
        tenant=request.user.tenant,
        is_current=True,
        is_deleted=False,
    ).select_related('uploaded_by').order_by('-uploaded_at')

    data = [{
        'id': doc.id,
        'file_name': doc.file_name,
        'category': doc.category,
        'category_display': doc.get_category_display(),
        'version': doc.version,
        'status': doc.status,
        'status_display': doc.get_status_display(),
        'error_message': doc.error_message,
        'uploaded_by': doc.uploaded_by.full_name if doc.uploaded_by else '未知',
        'uploaded_at': doc.uploaded_at.strftime('%Y-%m-%d %H:%M:%S'),
        'parsed_at': doc.parsed_at.strftime('%Y-%m-%d %H:%M:%S') if doc.parsed_at else None,
    } for doc in docs]

    return Response({
        'count': len(data),
        'documents': data,
    }, status=200)


@api_view(['POST'])
def upload_new_version(request, doc_id):
    """
    文档新版本上传接口
    功能：对已有文档新增版本，维护文档版本链
    流程：
        1. 校验租户、上传权限
        2. 校验原文档是否存在、属于当前租户、是生效版本、未删除
        3. 序列化器校验文件，生成新版本记录，自动把旧版本标记为非当前版本
        4. 新版本解析、向量化交给异步任务处理
    """
    # 校验租户状态
    check_tenant_active(request.user)

    # 权限校验
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限上传文档'}, status=403)

    # 查询原文档，必须是租户下生效、未删除的当前版本
    old_doc = Document.objects.filter(
        id=doc_id,
        tenant=request.user.tenant,
        is_current=True,
        is_deleted=False,
    ).first()

    if not old_doc:
        return Response({
            'error': '文档不存在、已删除，或不是当前版本'
        }, status=404)

    # 序列化器校验，传入旧文档对象用于版本链关联
    serializer = DocumentNewVersionSerializer(
        data=request.data,
        context={'request': request, 'old_doc': old_doc}
    )

    if serializer.is_valid():
        new_doc = serializer.save()
        return Response({
            'message': '新版本上传成功，正在后台解析',
            'document': {
                'id': new_doc.id,
                'file_name': new_doc.file_name,
                'version': new_doc.version,
                'status': new_doc.status,
            }
        }, status=201)
    return Response(serializer.errors, status=400)


@api_view(['GET'])
def document_versions(request, doc_id):
    """
    查询文档全版本链接口
    功能：获取一份文档从初始版本到所有迭代版本的完整版本记录
    逻辑：
        1. 根据文档找到版本链根节点 root_id
        2. 查出根文档 + 所有挂载在根上的子版本
        3. 按版本号倒序排序返回
    """
    # 校验租户状态
    check_tenant_active(request.user)

    # 校验文档归属与是否删除
    doc = Document.objects.filter(
        id=doc_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).first()

    if not doc:
        return Response({'error': '文档不存在或不属于本公司'}, status=404)

    # 获取版本链根ID：如果当前文档是子版本，取父根ID；否则自身就是根
    root_id = doc.parent_doc_id or doc.id

    # 查询该版本链下所有子版本
    chain_versions = Document.objects.filter(
        tenant=request.user.tenant,
        parent_doc_id=root_id,
        is_deleted=False,
    ).select_related('uploaded_by')

    # 查询根文档
    root_version = Document.objects.filter(
        id=root_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).select_related('uploaded_by')

    # 合并根文档和子版本，按版本号倒序
    versions = sorted(
        list(chain_versions) + list(root_version),
        key=lambda x: x.version,
        reverse=True
    )

    data = [{
        'id': v.id,
        'file_name': v.file_name,
        'version': v.version,
        'is_current': v.is_current,
        'status': v.status,
        'status_display': v.get_status_display(),
        'uploaded_by': v.uploaded_by.full_name if v.uploaded_by else '未知',
        'uploaded_at': v.uploaded_at.strftime('%Y-%m-%d %H:%M:%S'),
        'parsed_at': v.parsed_at.strftime('%Y-%m-%d %H:%M:%S') if v.parsed_at else None,
    } for v in versions]

    return Response({
        'document_id': doc.id,
        'file_name': doc.file_name,
        'total_versions': len(data),
        'versions': data,
    }, status=200)


@api_view(['DELETE'])
def delete_document(request, doc_id):
    """
    文档版本链软删除接口
    功能：整条文档版本链批量软删除，同时联动清理下游资源
    流程：
        1. 租户、权限校验
        2. 找到版本链根节点，事务内批量标记整条链is_deleted=True
        3. 写入审计日志，记录操作人、删除文档、受影响版本数量
        4. Pro专业版：同步删除Milvus向量库内该文档链所有向量
        5. 调用FastAPI接口，清除该租户在内存中的BM25检索缓存
    说明：向量删除、缓存刷新加try-catch，下游服务异常不阻塞主删除流程
    """
    from accounts.models import AuditLog

    # 校验租户状态
    check_tenant_active(request.user)

    # 权限校验
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限删除文档'}, status=403)

    # 查找目标文档
    doc = Document.objects.filter(
        id=doc_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).first()

    if not doc:
        return Response({'error': '文档不存在或已被删除'}, status=404)

    # 获取版本链根ID
    root_id = doc.parent_doc_id or doc.id

    # 数据库事务：批量软删除整条版本链，保证原子性
    with transaction.atomic():
        affected = Document.objects.filter(
            Q(parent_doc_id=root_id) | Q(id=root_id),
            tenant=request.user.tenant,
            is_deleted=False,
        ).update(is_deleted=True)

        # 写入审计日志，留存操作记录，满足SaaS审计追溯需求
        AuditLog.objects.create(
            tenant=request.user.tenant,
            operator=request.user,
            target_user=None,
            action='delete_document',
            detail=f'{request.user.full_name} 删除了文档「{doc.file_name}」'
                   f'（版本链共 {affected} 个版本）',
        )

    # 【下游联动1】Pro版本清理Milvus向量数据
    try:
        if request.user.tenant.plan_type == 'pro':
            milvus_client = get_milvus_client()

            if milvus_client:
                collection_name = f"tenant_{request.user.tenant_id}"
                # 重新查询整条版本链所有文档ID，用于过滤删除向量
                chain_ids = list(Document.objects.filter(
                    Q(parent_doc_id=root_id) | Q(id=root_id),
                    tenant=request.user.tenant
                ).values_list('id', flat=True))

                # 集合存在才执行删除
                if collection_name in milvus_client.list_collections():
                    milvus_client.delete(
                        collection_name=collection_name,
                        filter=f"document_id in {chain_ids}"
                    )
                    logger.info(f"已清理删除文档链的向量数据: {chain_ids}")
    except Exception as e:
        logger.error(f"清理 Milvus 向量失败: {e}")

    # 【下游联动2】通知FastAPI，刷新租户BM25内存缓存，防止检索到已删除文档
    try:
        fastapi_url = os.getenv("FASTAPI_URL", "http://fastapi-app:9000")
        refresh_url = f"{fastapi_url}/api/refresh_cache?tenant_id={request.user.tenant_id}"
        # 3秒超时，避免FastAPI服务异常阻塞Django主流程
        requests.post(refresh_url, timeout=3)

        logger.info(f"已成功通知 FastAPI 清理租户 {request.user.tenant_id} 的 BM25 缓存")
    except Exception as e:
        logger.warning(f"通知 FastAPI 清理缓存失败: {e}")

    return Response({
        'message': '文档链已删除',
        'deleted_versions': affected,
    }, status=200)
