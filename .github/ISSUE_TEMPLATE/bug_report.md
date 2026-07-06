---
name: "🐛 Bug Report (BE)"
about: API 500 에러, DB 크래시, AHP 알고리즘 오류 등 백엔드 버그를 보고합니다.
title: "[BUG] "
labels: bug, backend
assignees: ""
---

## 📝 버그 개요
어떤 백엔드 오동작이 발생했는지 작성해 주세요.

## 🔄 발생 시점 및 재현 방법
1. 호출 엔드포인트: [예: `POST /api/ahp/calculate`]
2. 전달한 Request Body / Parameter JSON
3. 발생한 HTTP Status Code 및 백엔드 에러 로그

## 🎯 기대 동작
정상적인 경우 반환되었어야 할 데이터 구조나 행정 로직을 설명해 주세요.

## 💻 실행 및 연동 환경
*   **Database**: [예: PostgreSQL 15 + PostGIS]
*   **Python Version**: [예: Python 3.10]
*   **FastAPI Version**: [예: FastAPI 0.100]

## 🔍 로그 및 상세 분석
FastAPI 터미널 로그 스택 트레이스나 오류 판단에 도움이 될 만한 SQL 쿼리 등을 공유해 주세요.
