from rest_framework.decorators import api_view
from rest_framework.response import Response
from accounts.utils import check_tenant_active
from .serializers import DocumentUploadSerializer, DocumentNewVersionSerializer
from .models import Document
from django.db import transaction
from django.db.models import Q

# 上传新文档 接口
@api_view(['POST'])
def upload_document(request):
    
    # 租户状态校验
    check_tenant_active(request.user)

    # 权限校验：只有管理员能上传
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限上传文档'}, status=403)

    # 调序列化器校验并保存
    serializer = DocumentUploadSerializer(
        data=request.data,
        context={'request': request}
    )
    if serializer.is_valid():
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
    return Response(serializer.errors, status=400)

# 文档查询 接口
@api_view(['GET'])
def list_documents(request):
    # 租户状态检验
    check_tenant_active(request.user)
    # 查询：当前版本以及未软删的文档
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
    }for doc in docs]

    return Response({
        'count': len(data),
        'documents': data,
    }, status=200)

# 文档新版本上传 接口
@api_view(['POST'])
def upload_new_version(request, doc_id):
    
    # 校验租户状态
    check_tenant_active(request.user)

    # 权限校验
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限上传文档'}, status=403)

    # 查旧文档
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

    # 校验 + 保存
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

# 文档历史版本查询 接口
@api_view(['GET'])
def document_versions(request, doc_id):

    # 1. 校验租户状态
    check_tenant_active(request.user)

    # 2. 查文档
    doc = Document.objects.filter(
        id=doc_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).first()

    if not doc:
        return Response({'error': '文档不存在或不属于本公司'}, status=404)

    # 3. 确定版本链的根 ID
    root_id = doc.parent_doc_id or doc.id

    # 4. 查同一版本链的所有版本
    chain_versions = Document.objects.filter(
        tenant=request.user.tenant,
        parent_doc_id=root_id,
        is_deleted=False,
    ).select_related('uploaded_by')

    root_version = Document.objects.filter(
        id=root_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).select_related('uploaded_by')

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

# 软删除文档链 接口
@api_view(['DELETE'])
def delete_document(request, doc_id):
    from accounts.models import AuditLog

    # 1. 租户状态校验
    check_tenant_active(request.user)

    # 2. 权限校验
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限删除文档'}, status=403)

    # 3. 查文档
    doc = Document.objects.filter(
        id=doc_id,
        tenant=request.user.tenant,
        is_deleted=False,
    ).first()

    if not doc:
        return Response({'error': '文档不存在或已被删除'}, status=404)

    # 4. 确定版本链的根 ID
    root_id = doc.parent_doc_id or doc.id

    with transaction.atomic():
        affected = Document.objects.filter(
            tenant=request.user.tenant,
            parent_doc_id=root_id,
            is_deleted=False,
        ).update(is_deleted=True)

        # 5. 写审计日志
        AuditLog.objects.create(
            tenant=request.user.tenant,
            operator=request.user,
            target_user=None,
            action='delete_document',
            detail=f'{request.user.full_name} 删除了文档「{doc.file_name}」'
                   f'（版本链共 {affected} 个版本）',
        )

    return Response({
        'message': '文档链已删除',
        'deleted_versions': affected,
    }, status=200)