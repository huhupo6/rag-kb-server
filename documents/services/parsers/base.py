# 抽象基类
from abc import ABC, abstractmethod

class BaseParser(ABC):
    """所有文档解析器的抽象基类"""
    
    @abstractmethod
    def parse(self, file_path: str) -> str:
        """解析文件并返回纯文本"""
        pass