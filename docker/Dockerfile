FROM harbor.innotech-stage.com/forex-infra/python:3.12-bookworm

WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

# 安装 JDK 17 和依赖
RUN apt-get update --fix-missing && apt-get install -y \
    default-mysql-client \
    curl \
    unzip \
    wget \
    gnupg \
    apt-transport-https \
    software-properties-common \
    openjdk-17-jdk \
    nodejs \
    npm \
    && apt-get clean && rm -rf /var/lib/apt/lists/*



# 根据平台设置 JAVA_HOME
ARG TARGETARCH
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-${TARGETARCH}
ENV PATH="$JAVA_HOME/bin:$PATH"


# 安装 Allure CLI
ENV ALLURE_VERSION=2.29.0
RUN curl -o allure-commandline-$ALLURE_VERSION.tgz -Ls https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/$ALLURE_VERSION/allure-commandline-$ALLURE_VERSION.tgz \
 && tar -zxvf allure-commandline-$ALLURE_VERSION.tgz -C /opt/ \
 && rm allure-commandline-$ALLURE_VERSION.tgz \
 && ln -s /opt/allure-$ALLURE_VERSION/bin/allure /usr/bin/allure

# 升级 pip 并安装 Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# 拷贝项目
COPY . /app

# 入口脚本
RUN chmod +x docker/entrypoint.sh
ENTRYPOINT ["docker/entrypoint.sh"]