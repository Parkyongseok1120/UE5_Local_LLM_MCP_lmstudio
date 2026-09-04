# 구형 평가 코드와 지원 범위

현재 사용하지 않는 실행·평가 코드가 남아 있는 폴더입니다. 기본 테스트와 설치·배포에는 포함되지 않으며 지금 실행 가능한 프로그램이라고 볼 수 없습니다.

예전 문서와 지시문은 정리했습니다. 당시 설명이 필요하면 Git 기록에서 확인해야 합니다. 이 폴더를 `PYTHONPATH`에 넣거나 MCP 실행 위치로 지정하지 않습니다.

현재 지원하는 진입점은 다음과 같습니다.

- 검색 서버: `scripts/unreal_rag_direct.py`
- 파일·빌드 서버: `lmstudio-unreal-agent-mcp/src/direct-server.js`
- 별도 세션 관리: `lmstudio-unreal-agent-mcp/src/strict-server.js`
- 대화 압축기: `lmstudio-context-compactor-plugin/src/index.ts`

현재 사용법은 [README](../README.md)를 보면 됩니다.
