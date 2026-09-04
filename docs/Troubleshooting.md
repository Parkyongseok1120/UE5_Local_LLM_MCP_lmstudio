# 설치·검색·파일 수정·빌드 오류 해결

먼저 어떤 프로젝트와 엔진을 보고 있는지 확인해야 합니다. 설치 위치를 옮겼거나 버전을 바꾼 뒤 생긴 문제라면 현재 폴더에서 설치기를 다시 실행하고 LM Studio를 재시작하는 것부터 확인하면 됩니다.

```powershell
python install.py --profile standard --yes
.\rag.ps1 doctor
```

위 명령은 읽기 전용 설정입니다. 수정·빌드를 유지하려면 원래 쓰던 권한 옵션을 함께 지정해야 합니다.

## Python·패키지를 찾지 못하는 오류

Python이 없으면 `install.py`를 직접 실행하지 말고 `INSTALL.bat` 또는 `./install.sh`를 사용해야 합니다. 필요한 Python을 사용자 폴더에 준비합니다. 첫 설치에서는 `--skip-runtime-bootstrap`, `--skip-deps`를 빼야 합니다.

초기 다운로드가 실패하면 압축 배포본 파일이 모두 있는지와 GitHub Releases 접속을 확인합니다. 최소 Ubuntu 환경에는 `ca-certificates curl tar coreutils`가 필요합니다.

`@modelcontextprotocol/sdk/server/index.js`를 못 찾으면 의존 패키지가 없는 상태입니다. `--skip-deps` 없이 다시 설치해야 합니다.

## 검색 대상 프로젝트 불일치

같은 이름의 복사본이 있으면 `.uproject` 전체 경로로 지정합니다. 엔진 설명만 필요하면 `scope=engine`, 내 코드가 필요하면 정확한 프로젝트와 `scope=project`, 둘 다 필요하면 `scope=mixed`를 사용합니다.

`RAG_RAW_MULTI_ENGINE_CORPUS`, `RAG_MULTI_ENGINE_QUERY_UNSUPPORTED`, `RAG_ENGINE_INDEX_MISMATCH`는 엔진이 다른 자료를 섞었거나 맞는 자료가 없다는 뜻입니다. 엔진별로 다시 만들고 나눠서 조회해야 합니다.

코드나 `Build.cs`를 바꿨다면 자료를 갱신합니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
```

검색에서 못 찾았다는 이유만으로 파일이나 기능이 없다고 단정하지 말아야 합니다. `search_files`와 직접 읽기로 확인해야 합니다.

## 에디터 내보내기 자료 누락

기본 `refresh`는 프로젝트 소스를 갱신하며 에디터를 열지 않습니다. `-RefreshScope editor_metadata`는 이미 내보낸 자료만 읽습니다. 에디터 실행까지 의도한 경우에만 `-AllowEditorLaunch`를 추가해야 합니다. 내보낸 자료는 선택한 프로젝트의 `Saved/LmStudioMetadataExports`에 있어야 합니다.

블루프린트 노드 연결은 내보내기 플러그인이 없으면 빠질 수 있습니다. [에디터 자료 내보내기](Editor_Metadata_Export.md)를 참고해야 합니다.

## 파일 수정 권한과 충돌 오류

| 오류 | 원인과 대응 |
|---|---|
| `ALLOW_WRITE=0` | 읽기 전용입니다. 수정이 필요하면 설치에서 AGENT 권한을 켜야 합니다. |
| `FILE_VERSION_CONFLICT` | 읽은 뒤 파일이 바뀝니다. 현재 내용을 다시 읽고 수정안을 맞춰야 합니다. |
| `FILE_SNAPSHOT_REQUIRED` | `fileVersionReceipt` 또는 유효한 `expectedHash`를 안 보냅니다. |
| `FILE_SNAPSHOT_INVALID` | 확인값이 만료됐거나 서버 재시작 등으로 유효하지 않습니다. 다시 읽어야 합니다. |
| `FILE_SNAPSHOT_SCOPE_MISMATCH` | 다른 프로젝트·파일·대화의 확인값을 사용합니다. 정확한 대상을 다시 읽어야 합니다. |
| 기존 파일 생성 거절 | `write_file`은 새 파일용입니다. 기존 파일은 `replace_in_file`을 사용해야 합니다. |
| `rollback skipped ... (conflict)` | 복구 중 다른 수정이 발견됩니다. 현재 상태를 확인하기 전 덮어쓰지 말아야 합니다. |

시간 초과가 나면 같은 쓰기를 바로 반복하지 말아야 합니다. 실제 저장됐는지 먼저 읽어서 확인해야 합니다.

`semanticAdvisory`나 `static_validate_project`의 경고는 보조 진단입니다. 자동으로 수정을 취소하거나 빌드를 막지는 않습니다. 경고 내용을 확인하고 필요하면 고치거나 실제 빌드로 확인하면 됩니다.

예상치 않게 파일을 수정했다면 `python install.py --profile standard --yes`로 읽기 전용으로 돌릴 수 있습니다.

## 구형 설정의 권한·작업 단계 오류

기본 Direct에서 `CONTEXT_COMPACTOR_NOT_ACTIVE`, `TASK_AUTH_*`, `TASK_ROUTE_*`가 나오면 오래된 설정이나 프로세스를 확인해야 합니다. `unreal-rag`는 `scripts/unreal_rag_direct.py`, `unreal-agent`는 `src/direct-server.js`여야 합니다. 재설치 후 MCP를 완전히 재시작합니다. 임의의 승인값을 만들지 말아야 합니다.

별도로 사용하는 Node Strict의 `STRICT_SESSION_INVALID`는 그 대화의 세션 문제입니다. `strict_status`로 확인하고 새로 시작하거나, 중단된 세션을 이어가려면 사용자 승인 후 `strict_resume`을 사용합니다.

## 대화 길이 초과와 응답 오류

실제 모델이 선택됐는지 확인하고 `codex/unreal-context-compactor`는 기본 `OFF`로 두어야 합니다. 필요할 때 해당 채팅의 단일 스위치를 켭니다. `npm --prefix lmstudio-context-compactor-plugin run status`는 설치 파일 확인용이며 채팅 활성화를 증명하지 않습니다.

이미 대화 한도나 KV 캐시가 찼다면 새 채팅에 정확한 프로젝트, 현재 요청, 바꾼 파일, 마지막 빌드 결과, 남은 오류만 넘겨야 합니다.

`status=no_new_information`은 같은 결과를 짧게 돌려줬다는 뜻일 수 있습니다. 성공한 검색·읽기는 앞선 `repeatReceipt`를 보냈을 때만 줄여 줍니다. `repeatReceipt`를 보내지 않으면 원문을 다시 받습니다. 반복 실패는 줄여서 반환해도 여전히 실패입니다.

`OUTPUT_LIMIT_EXCEEDED`는 응답 범위가 너무 큰 경우입니다. 요청한 줄 수·파일 범위·결과 수를 줄여야 합니다. `nextDetailLevel`이 있으면 그 값으로 다시 요청할 수 있습니다.

## 빌드 오류와 로그 확인

`read_unreal_logs`에서 `mode=tail`은 최근 부분, `mode=first_error`는 최초 오류 탐색, `mode=range`와 `cursorByte`는 특정 범위 읽기에 사용합니다. `nextCursorByte`와 `hasMore`로 이어서 읽을 수 있습니다.

빌드와 자동화 테스트는 출력량과 실행 시간이 제한됩니다. `fullLogPath`도 앞뒤 일부만 담을 수 있으므로 생략된 바이트 수를 확인해야 합니다. `sourceTruncated=true`인데 원인을 전부 봤다고 결론내리지 않습니다.

`target=Editor`는 선택한 프로젝트의 에디터 대상을 찾습니다. 여러 대상이 모호하면 실제 발견된 이름을 지정해야 합니다. 빌드는 `ALLOW_UNREAL_BUILD=1`이 필요하지만 정적 검사를 먼저 통과할 필요는 없습니다.

## 코드 오류 유형별 확인 항목

`GENERATED_H_MISSING`은 생성 헤더, `C1083_MISSING_INCLUDE`는 포함 파일, `LNK_MISSING_CPP_DEFINITION`은 선언한 함수의 구현 여부부터 확인하면 됩니다. 분류 코드는 `scripts/error_taxonomy.py`와 `collect_build_logs.py`에서 사용합니다.

`UHT_MACRO_IN_CONDITIONAL_BLOCK`은 언리얼 선언 매크로가 잘못된 전처리 조건 안에 있는지 확인해야 합니다. `GENGINE_WORLD_CONTEXT`는 `GEngine->GetWorld()` 대신 해당 객체가 속한 월드나 명시적으로 받은 `UWorld*`를 사용해야 하는지 확인합니다.

헤더의 주석과 선언을 읽지 않고 조기 반환을 버그로 단정하지 말아야 합니다. 빌드 성공만으로 실행 중 문제가 해결됐다고 말하지도 말아야 합니다. 실제로 확인한 검사·로그·재현 결과를 구분해서 적으면 됩니다.
