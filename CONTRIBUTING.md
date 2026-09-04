# 기여 안내

저장소를 복제하고 [설치 안내](README.md)대로 준비하면 됩니다. 개발 환경은 Python 3.12와 Node.js 20을 기준으로 합니다.

## 변경 검증 절차

코드를 바꿨다면 관련 검사를 실행하고, 공개 전에는 다음 전체 검사를 확인해야 합니다. 문서만 고친 경우에는 링크·경로·인코딩·배포 문서 구성을 우선 확인하면 됩니다.

```powershell
python -m pip install -r requirements-dev.txt ruff
python -m compileall -q install.py installer scripts skills tools
python -m pytest -q --tb=short
python scripts/verify_encoding.py
ruff check scripts/ tests/ --select=E,F,W --ignore=E501,E402,F401
node --test scripts/chat_history_trim.test.js scripts/stage_campaign_verify.test.js
```

각 Node 구성의 폴더에서 잠금 파일대로 설치하고 검사를 실행합니다. 대화 압축기의 `npm test`에는 TypeScript 빌드도 포함됩니다.

```powershell
npm --prefix lmstudio-unreal-agent-mcp ci --no-fund --no-audit
npm --prefix lmstudio-unreal-agent-mcp test
npm --prefix lmstudio-context-compactor-plugin ci --no-fund --no-audit
npm --prefix lmstudio-context-compactor-plugin test
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\installer_support\Verify-Oss-Ready.ps1
```

자동 검사와 실제 장비에서 실행한 결과를 구분해서 적어야 합니다. 실제 실행 결과에는 장비·엔진·프로젝트·명령과 결과를 남깁니다.

## 커밋·배포 제외 파일

- `config/workspace.json`과 로컬 `agent-mcp.json` 같은 개인 경로 설정
- `PORTABLE_ROOT.txt`, API 키, 개인 경로가 든 파일
- 생성한 검색 데이터베이스, `*.sqlite`, 원본 수집 자료
- 비공개 프로젝트의 로그·스냅샷·`Reports/` 결과
- 라이선스가 있는 엔진 소스와 이를 담은 검색 자료

이미 추적 중인 공개용 `data/baseline` 시험 자료 외에는 로컬 자료를 추가하지 말아야 합니다. 무시된 파일에 `git add -f`를 쓰지 않습니다. 잘못 올릴 준비를 했다면 다음처럼 준비 상태만 취소할 수 있습니다.

```powershell
git restore --staged config/workspace.json
git restore --staged PORTABLE_ROOT.txt
```

## 코드 및 문서 작성 기준

- 설명은 한글 자연어로 적으며 문장 끝은 ‘~합니다’, ‘~됩니다’, ‘~해야 합니다’로 통일합니다. 무엇을 하는지 먼저 설명하고 실제 명령·설정 이름만 원문으로 둡니다.
- 제목은 기능·대상·절차를 바로 알 수 있는 구체적인 명사형으로 작성합니다. 본문은 존댓말 문장으로 설명합니다.
- 문서는 같은 내용을 여러 벌 유지하지 않습니다. 지난 계획이나 일회성 검사 기록은 현재 사용법에 섞지 말아야 합니다.
- Python은 표준 라이브러리를 우선 사용합니다. 외부 실행 의존성을 늘릴 때는 필요성을 설명합니다.
- PowerShell은 오류 처리를 명시하고 지원 버전에서 확인합니다.
- Node 서버는 기존 CommonJS 구성을 따릅니다. TypeScript를 사용하는 압축기는 해당 구성을 따릅니다.
- 언리얼 프로젝트에서는 네임스페이스를 웬만하면 추가하지 말아야 합니다.
- 같은 이름의 프로젝트 복사본과 다른 엔진 버전이 섞이지 않는지 확인합니다.

변경 설명에는 해결한 문제, 바뀐 동작, 실제로 한 검사를 적으면 됩니다. 보안 제보는 [SECURITY.md](SECURITY.md), 엔진 자료 취급은 [EPIC_NOTICE.md](EPIC_NOTICE.md)에 있습니다.
