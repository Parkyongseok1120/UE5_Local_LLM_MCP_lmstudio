# Rider·Cline 설치 후 점검 항목

필요한 기능만 골라 확인하면 됩니다. 기본 `Direct`에서는 모델이 작업 순서를 정하며, 이 목록을 전부 실행해야 사용할 수 있는 것은 아닙니다.

## 연결과 읽기

```powershell
.\rag.ps1 doctor
```

- [ ] `unreal-rag`가 `scripts/unreal_rag_direct.py`를 실행합니다.
- [ ] `unreal-agent`가 `src/direct-server.js`를 실행합니다.
- [ ] 설치용 임시 경로 표시가 남아 있지 않고 다른 MCP 설정도 보존됩니다.
- [ ] `unreal_get_active_project`가 의도한 프로젝트 또는 미선택 상태를 반환합니다.
- [ ] `unreal_rag_health`가 검색 자료 상태를 반환합니다.
- [ ] `get_workspace_info`에서 프로젝트 경로와 권한이 맞습니다.
- [ ] 파일을 읽으면 내용·SHA-256·`fileVersionReceipt`가 반환됩니다.
- [ ] 다른 대화에서도 첫 대화의 작업을 취소하지 않고 읽기·검색이 가능합니다.

## 파일 수정과 충돌 처리 점검

실제 작업에 영향 없는 주석 한 줄처럼 되돌릴 수 있는 대상으로 확인해야 합니다.

- [ ] 현재 파일을 읽고 받은 `fileVersionReceipt`로 `replace_in_file`을 실행합니다. `expectedOccurrences=1`을 사용합니다.
- [ ] 오래된 확인값은 `FILE_VERSION_CONFLICT`로 거절되고 파일을 덮어쓰지 않습니다.
- [ ] 첫 수정 결과의 새 확인값으로 원래 내용으로 되돌릴 수 있습니다.
- [ ] 누락·만료·다른 프로젝트의 값은 `FILE_SNAPSHOT_REQUIRED`, `FILE_SNAPSHOT_INVALID`, `FILE_SNAPSHOT_SCOPE_MISMATCH`로 거절됩니다.

`expectedHash`도 호환 입력으로 가능합니다. 서버가 이전 값을 자동으로 선택하지는 않습니다. `apply_edit_bundle`은 서로 다른 기존 파일 1~2개의 작은 수정에만 쓰며 새 파일 생성에는 사용할 수 없습니다.

## 빌드 실행과 결과 확인

- [ ] 보조 검사 `static_validate_project`가 빌드 허가증을 요구하거나 발급하지 않습니다.
- [ ] Rider 또는 `build_unreal_project`가 정확한 프로젝트·엔진으로 실행됩니다.
- [ ] `target=Editor`가 해당 프로젝트의 에디터 대상을 선택합니다.
- [ ] 결과에 실제 성공·실패와 로그 경로가 있습니다. 생략된 출력은 생략됐다고 표시됩니다.
- [ ] 실행 시간 초과 시 자식 프로세스까지 종료됩니다.

삭제 기능은 이 점검을 위해 실행할 필요 없습니다. 필요한 삭제에만 제안 토큰, 파일 확인값, 삭제 권한, 실제 사용자 승인을 적용해야 합니다.

관련 규칙은 [Cline 지시문](../prompts/cline_unreal_agent_system.md)과 [도구 사용 규칙](LMStudio_MCP_Tool_Discipline.md)에 있습니다.
