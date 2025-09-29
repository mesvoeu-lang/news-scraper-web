#!/bin/bash

# 서버 배포 스크립트
echo "🚀 뉴스 스크래퍼 서버 배포 시작..."

# Docker 이미지 빌드
echo "📦 Docker 이미지 빌드 중..."
docker build -t news-scraper .

# 기존 컨테이너 중지 및 제거
echo "🛑 기존 컨테이너 중지 중..."
docker-compose down

# 새 컨테이너 시작
echo "▶️ 새 컨테이너 시작 중..."
docker-compose up -d

# 상태 확인
echo "✅ 배포 완료!"
echo "🌐 서버 주소: http://your-server-ip:8000"
echo "📊 컨테이너 상태:"
docker-compose ps
