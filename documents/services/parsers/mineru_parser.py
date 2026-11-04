import os
import logging
import requests
from .base import BaseParser

logger = logging.getLogger(__name__)

class MinerUParser(BaseParser):
    """基于 MinerU 微服务的文档解析器 (支持 PDF/DOCX)"""

    def __init__(self):
        self.api_url = "http://mineru-api:8000/v1/file_parse"

    def parse(self, file_path: str) -> str:
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ['.pdf', '.docx', '.doc']:
                raise ValueError(f"MinerU 不支持该格式: {ext}")

            params = {
                'response_format': 'json',
                'wait_timeout': 600,
                'ocr_mode': 'auto',
                'output_formats': ["markdown"]
            }

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
            response.raise_for_status()

            result = response.json()
            # 调试时可以解除下面这行的注释，平时不用打印，不然日志太长
            # logger.info(f"========= MinerU返回的原始数据: {result} =========")

            # 🌟 核心修改：精准提取 files[0].content.markdown
            markdown_text = ""
            try:
                if 'files' in result and len(result['files']) > 0:
                    file_data = result['files'][0]
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