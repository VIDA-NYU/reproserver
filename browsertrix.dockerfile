FROM webrecorder/browsertrix-crawler@sha256:b9822b7bd699748903e8f6663775442292263599e62f00d6e402e0eac145211c

RUN \
    apt-get update && \
    apt-get install unzip && \
    rm -rf /var/lib/apt/lists/*
RUN \
    curl -Lo /tmp/rclone.zip https://downloads.rclone.org/v1.58.1/rclone-v1.58.1-linux-amd64.zip && \
    unzip -p /tmp/rclone.zip > /usr/local/bin/rclone && \
    chmod +x /usr/local/bin/rclone && \
    rm /tmp/rclone.zip
