# 内置标准库
import os
import zipfile
import pymupdf as fitz
import logging
import re
import hashlib

# Django
from django.conf import settings
from django.db import transaction

# DRF
from rest_framework import serializers

# 本地业务模块
from .models import Document
from .tasks import parse_document

logger = logging.getLogger(__name__)

# 魔数字典：key=文件后缀，value=对应合法二进制文件头
# txt无固定文件头，为空元组，跳过魔数校验
MAGIC_NUMBERS = { 
    ".pdf": (b"%PDF-",), 
    ".docx": (b"PK\x03\x04",), 
    ".txt": (),  
    }

# 安全安全清洗函数
def save_uploaded_file(file_obj, doc_id):
    """
    安全保存上传文件工具函数
    - 使用 basename 剥离危险路径前缀，防御路径穿越
    - 使用文档 ID 嵌入文件名，避免同名文件互相覆盖
    - 自动创建存储目录，分块写入大文件，减少内存压力
    - 流式计算 SHA256，用于后续秒传去重
    :param file_obj: DRF 上传文件对象
    :param doc_id: 文档主键，用于生成唯一文件名
    :return: relative_path(相对存储路径), full_path(磁盘绝对路径)
    """
    # 拿到原始文件名，剥离前面所有路径，只保留文件名本身，防御路径穿越
    raw_name = os.path.basename(file_obj.name)

    # 如果拿到的是空字符串（比如用户输入纯 ../ 这种），直接抛异常阻断
    if not raw_name:
        raise ValueError("文件名非法")

    # 正则替换：把 Windows / Linux 系统不允许在文件名里出现的特殊字符，统一替换成下划线 _
    # 禁止字符：< > : " / \ | ? *
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', raw_name)

    # 截断文件名，最长128字符，防止超长文件名导致存储报错
    safe_name = safe_name[:128]

    # 拼接相对存储路径：documents目录 + doc_id_清洗后的文件名
    # 带上doc_id，就算原文件名一样，最终文件名称也不会重复，避免覆盖
    relative_path = os.path.join('documents', f'{doc_id}_{safe_name}')

    # 拼接磁盘绝对路径：MEDIA_ROOT是Django配置的静态文件根目录
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)

    # 递归创建文件夹，目录已经存在也不会报错（exist_ok=True）
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # 初始化 SHA256 哈希对象
    sha256_hash = hashlib.sha256()

    # 二进制写入模式打开文件
    with open(full_path, 'wb+') as f:
        # DRF的文件对象分块迭代写入，不会一次性把整个大文件加载进内存，适合大文件上传
        for chunk in file_obj.chunks():
            f.write(chunk)
            sha256_hash.update(chunk)

    file_hash = sha256_hash.hexdigest()

    # 返回三个值：相对路径（存数据库）、磁盘绝对路径（程序读取用）、清洗后的文件名（展示给前端）、文件哈希
    return relative_path, full_path, safe_name, file_hash

# 全新文档上传序列化器
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
        # 拿到文件后缀，转小写，统一判断
        ext = os.path.splitext(value.name)[1].lower()
        # 后缀白名单：只允许 pdf / docx / txt
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')
        # 限制单文件最大 50MB
        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')

        # 魔数校验
        magic_list = MAGIC_NUMBERS[ext]
        if magic_list:
            try:
                # 读取前 8 字节，足够判断文件头
                header = value.read(8)
                # 指针必须归位，否则后续保存文件时会丢失文件头的前 8 个字节
                value.seek(0)
            except Exception as e:
                raise serializers.ValidationError("文件读取失败，请重新上传")
            
            # 匹配任意一个合法魔数即通过
            match = any(header.startswith(magic) for magic in magic_list)
            if not match:
                raise serializers.ValidationError(f"文件后缀与真实内容不匹配，禁止上传伪装的 {ext} 文件")
        # txt 无固定魔数，直接放行

        # 损坏 / 加密文件预检
        try:
            if ext == '.pdf':
                doc = fitz.open(stream=value, filetype="pdf")
                if doc.is_encrypted:
                    raise serializers.ValidationError("该 PDF 文件已加密，请解除密码后上传")
                doc.close()
            elif ext == '.docx':
                with zipfile.ZipFile(value) as z:
                    bad_file = z.testzip()
                    if bad_file is not None:
                        raise serializers.ValidationError("DOCX 文件已损坏或格式不完整")
            elif ext == '.txt':
                # TXT 预检：防空文件、编码异常、二进制伪装
                head_bytes = value.read(4096)
                if not head_bytes.strip():
                    raise serializers.ValidationError("TXT 文件内容为空，无法解析")

                is_valid_text = False
                for encoding in ['utf-8', 'gbk', 'gb18030']:
                    try:
                        decoded_str = head_bytes.decode(encoding)
                        control_chars = sum(1 for c in decoded_str if ord(c) < 32 and c not in '\n\r\t')
                        if control_chars > len(decoded_str) * 0.05:
                            continue
                        is_valid_text = True
                        break
                    except UnicodeDecodeError:
                        continue
                if not is_valid_text:
                    raise serializers.ValidationError("TXT 文件编码异常或疑似二进制文件伪装，请检查文件内容")
        except serializers.ValidationError as e:
            raise e
        except Exception:
            raise serializers.ValidationError("文件校验失败，可能已损坏")
        finally:
            value.seek(0)

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

        # 统计该租户下，正在生效、没有软删除的文档数量
        current_docs_count = Document.objects.filter(
            tenant=tenant,
            is_current=True,
            is_deleted=False
        ).count()

        # 达到配额上限，拒绝上传
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
                # 先创建文档记录，初始file_name、file_path为空
                doc = Document.objects.create(
                    tenant=user.tenant,
                    category=validated_data['category'],
                    file_name='',
                    file_path='',
                    version=1,
                    parent_doc_id=None,
                    is_current=True,
                    status='pending',
                    uploaded_by=user,
                )

                # 调用文件保存函数，拿到清洗后的文件名、相对路径、绝对路径
                relative_path, full_path, safe_name, file_hash = save_uploaded_file(validated_data['file'], doc.id)
                # 租户秒传去重校验
                existing_doc = Document.objects.filter(
                    tenant=user.tenant,
                    file_hash=file_hash,
                    is_current=True,
                    is_deleted=False
                ).exclude(id=doc.id).first()

                if existing_doc:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                    raise serializers.ValidationError(f"该文件已存在（文档名：{existing_doc.file_name}），请勿重复上传。")

                # 回填清洗后的文件名和存储路径
                doc.file_name = safe_name
                doc.file_path = relative_path
                doc.file_hash = file_hash
                doc.save(update_fields=['file_name', 'file_path', 'file_hash'])

                # 事务提交成功之后，投递Celery异步任务
                # 避免回滚后出现无效任务
                transaction.on_commit(lambda: parse_document.delay(doc.id))

                return doc
            
        except Exception as e:
            # 异常兜底：事务回滚，清理已经落地到磁盘的孤儿文件
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e

# 文档新版本上传序列化器
class DocumentNewVersionSerializer(serializers.Serializer):
    """
    文档新版本上传序列化器
    用于对已有文档上传新版本，维护文档版本链
    逻辑：旧文档标记为非当前版本，新建一条版本+1 的记录作为当前生效文档
    """
    file = serializers.FileField(label='新版本文件')

    def validate_file(self, value):
        """
        新版本文件校验，和新建文档校验保持一致：后缀 + 大小 + 魔数校验，安全口径统一
        """
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.pdf', '.docx', '.txt']:
            raise serializers.ValidationError('仅支持 PDF / DOCX / TXT 格式')

        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError('文件不能超过 50MB')

        magic_list = MAGIC_NUMBERS[ext]
        if magic_list:
            try:
                # 读取前 8 字节，足够判断文件头
                header = value.read(8)
                # 【关键】指针必须归位，否则后续保存文件时会丢失文件头的前 8 个字节
                value.seek(0)
            except Exception as e:
                raise serializers.ValidationError("文件读取失败，请重新上传")
            
            match = any(header.startswith(magic) for magic in magic_list)
            if not match:
                raise serializers.ValidationError(f"文件后缀与真实内容不匹配，禁止上传伪装的 {ext} 文件")

        # 损坏 / 加密文件预检
        try:
            if ext == '.pdf':
                doc = fitz.open(stream=value, filetype="pdf")
                if doc.is_encrypted:
                    raise serializers.ValidationError("该 PDF 文件已加密，请解除密码后上传")
                doc.close()
            elif ext == '.docx':
                with zipfile.ZipFile(value) as z:
                    bad_file = z.testzip()
                    if bad_file is not None:
                        raise serializers.ValidationError("DOCX 文件已损坏或格式不完整")
            elif ext == '.txt':
                # TXT 预检：防空文件、编码异常、二进制伪装
                head_bytes = value.read(4096)
                if not head_bytes.strip():
                    raise serializers.ValidationError("TXT 文件内容为空，无法解析")

                is_valid_text = False
                for encoding in ['utf-8', 'gbk', 'gb18030']:
                    try:
                        decoded_str = head_bytes.decode(encoding)
                        control_chars = sum(1 for c in decoded_str if ord(c) < 32 and c not in '\n\r\t')
                        if control_chars > len(decoded_str) * 0.05:
                            continue
                        is_valid_text = True
                        break
                    except UnicodeDecodeError:
                        continue
                if not is_valid_text:
                    raise serializers.ValidationError("TXT 文件编码异常或疑似二进制文件伪装，请检查文件内容")
        except serializers.ValidationError as e:
            raise e
        except Exception:
            raise serializers.ValidationError("文件校验失败，可能已损坏")
        finally:
            value.seek(0)
        
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
                # 旧版本下线，不再是当前生效版本
                old_doc.is_current = False
                old_doc.save(update_fields=['is_current'])

                # 创建新版本记录，继承分类、归属，版本号 +1
                new_doc = Document.objects.create(
                    tenant=user.tenant,
                    category=old_doc.category,
                    file_name='',
                    file_path='',
                    version=old_doc.version + 1,
                    parent_doc_id=old_doc.parent_doc_id or old_doc.id,
                    is_current=True,
                    status='pending',
                    uploaded_by=user,
                )

                # 调用保存函数，拿到清洗后的文件名、路径
                relative_path, full_path, safe_name, file_hash = save_uploaded_file(validated_data['file'], new_doc.id)
                # 版本链去重校验
                if old_doc.file_hash == file_hash:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                    raise serializers.ValidationError("新版本文件与原文件内容完全相同，无需更新。")
                
                new_doc.file_path = relative_path
                new_doc.file_name = safe_name
                new_doc.file_hash = file_hash
                new_doc.save(update_fields=['file_path', 'file_name', 'file_hash'])

                # 事务提交后 发起解析任务
                transaction.on_commit(lambda: parse_document.delay(new_doc.id))

                return new_doc
            
        except Exception as e:
            # 异常兜底清理孤儿文件
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.warning(f"事务回滚，已清理孤儿文件: {full_path}")
                except Exception as remove_err:
                    logger.error(f"清理文件失败: {remove_err}")
            raise e
