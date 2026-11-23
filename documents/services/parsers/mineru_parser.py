# mineru_parser.py
# 功能：文档解析器实现类，MinerUParser对接MinerU微服务解析PDF/DOCX，继承BaseParser
# 输出：原始Markdown文本，交给clean_markdown清洗
# 特点：支持跨页表格识别、版面还原、OCR自动模式

# Python内置标准库
import os
import logging

# 第三方库
import requests

# 当前app自定义模块
from .base import BaseParser

logger = logging.getLogger(__name__)


# 文档PDF、Docx、Doc格式解析
class MinerUParser(BaseParser):
    """
    基于 MinerU 微服务的文档解析器
    支持 PDF / DOCX / DOC，自动版面分析、跨页表格合并、OCR识别，输出markdown
    """
    def __init__(self):
        # MinerU微服务接口地址，容器内服务名访问
        self.api_url = "http://mineru-api:8000/v1/file_parse"

    def parse(self, file_path: str) -> str:
        """
        上传本地文件到MinerU微服务，获取解析后的markdown文本
        :param file_path: 文件本地绝对路径
        :return: 解析完成的markdown原始文本
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ['.pdf', '.docx', '.doc']:
                raise ValueError(f"MinerU 不支持该格式: {ext}")

            # MinerU调用参数：输出markdown、自动OCR、超时时间
            params = {
                'response_format': 'json',
                'wait_timeout': 600,
                'ocr_mode': 'auto',
                'output_formats': ["markdown"]
            }

            # 二进制上传文件
            with open(file_path, 'rb') as f:
                files = {
                    'files': (os.path.basename(file_path), f, 'application/octet-stream')
                }
                response = requests.post(
                    self.api_url,
                    params=params,
                    files=files,
                    timeout=700
                )
                # 请求状态码非200直接抛异常
                response.raise_for_status()

            result = response.json()

            # 调试时可以解除下面这行的注释，平时不用打印
            # logger.info(f"========= MinerU返回的原始数据: {result} =========")

            markdown_text = ""
            try:
                if 'files' in result and len(result['files']) > 0:
                    file_data = result['files'][0]
                    # 解析成功且携带markdown内容
                    if file_data.get('status') == 'completed' and 'content' in file_data:
                        markdown_text = file_data['content'].get('markdown', '')
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"解析返回的 JSON 结构异常: {e}")

            if not markdown_text or not markdown_text.strip():
                logger.error(f"MinerU 返回数据结构解析失败: {result}")
                raise ValueError("微服务提取内容为空")

            return markdown_text

        except requests.exceptions.RequestException as e:
            logger.error(f"调用 MinerU 微服务网络异常: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"MinerU返回body: {e.response.text}")
            raise ValueError(f"文档解析服务连接失败: {e}")
        except Exception as e:
            logger.error(f"MinerU 解析失败，路径: {file_path}, 错误: {str(e)}")
            raise ValueError(f"文档解析失败: {str(e)}")
