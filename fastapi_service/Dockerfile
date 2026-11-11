# Dockerfile.fastapi
# FastAPI 微服务镜像构建脚本
# 用于向量检索、RRF 融合、检索接口，独立于 Django 主后端
# 使用 uvicorn 启动，容器内监听 9000 端口
# 由主编排文件拉起，供主业务调用检索能力

# 使用官方 Python 3.11 镜像
FROM python:3.11-slim-bookworm

# 设置容器内的工作目录为 /app
WORKDIR /app

# 设置环境变量，防止 Python 写 pyc 文件
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 阿里云源
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 复制依赖文件并安装
COPY requirements.txt /app/
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 复制当前目录所有代码
COPY . /app/

# 暴露 FastAPI 服务的端口
EXPOSE 9000

# 启动 Uvicorn 服务器
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]