from setuptools import setup, find_packages

setup(
    name="kb_tokenizer",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "jieba==0.42.1",
    ],
    description="企业知识库全局统一分词器",
)