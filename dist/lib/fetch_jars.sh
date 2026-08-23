#!/usr/bin/env bash
# 下载worker运行期依赖（版本与实验时一致）
set -e
cd "$(dirname "$0")"
BASE=https://repo1.maven.org/maven2
curl -sO $BASE/redis/clients/jedis/5.2.0/jedis-5.2.0.jar
curl -sO $BASE/org/apache/commons/commons-pool2/2.12.0/commons-pool2-2.12.0.jar
curl -sO $BASE/org/slf4j/slf4j-api/2.0.13/slf4j-api-2.0.13.jar
echo "依赖下载完成"
