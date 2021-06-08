FROM ubuntu:bionic

#installing git
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y git

#installing python
RUN : \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository -y ppa:deadsnakes \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3.8-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && :

RUN set -xe \
    && apt-get update \
    && apt-get install python-pip
RUN pip install --upgrade pip

# Cloning the repo
RUN git clone https://github.com/rshero/YuiiChan /root/yui
WORKDIR /root/yui

ENV PATH="/root/yui/bin:$PATH"

# Install requirements
RUN pip3 install -U -r requirements.txt

# Starting Worker
CMD ["python3","-m","tg_bot"]
