# LM Studio 연결

설치는 세 부분으로 나뉩니다.

1. `~/.lmstudio/config-presets`에 `Evidence-First Code Audit` 설정을 설치합니다.
2. `~/.lmstudio/mcp.json`에 읽기 전용 `evidence-first` MCP를 등록합니다.
3. 프로젝트 파일·빌드·실행 도구는 별도로 연결합니다.

이 MCP는 프로젝트 파일을 읽거나 쓰지 않습니다. 근거 기록 규칙을 제공하고 작성한 기록의 형식을 검사합니다. 따라서 프로젝트 도구가 없어도 읽기 전용 `SAFE` 구성을 사용할 수 있습니다.

채팅에서 설치한 설정을 선택하고 `evidence-first` MCP를 켜야 합니다. 설치만으로 기존 채팅의 설정이 바뀌지는 않습니다. LM Studio 0.4 이상에서 API를 사용하면 `system_prompt`와 `mcp/evidence-first`를 전달합니다.

설치 후 `scripts/smoke_evidence_first_mcp.py`로 기본 연결을 확인합니다. 모델 답변 품질을 비교하려면 같은 모델과 같은 문제에 규칙을 켠 경우와 끈 경우를 비교해야 합니다. 연결 검사만 통과했다고 모델 품질이 좋아졌다고 말하면 안 됩니다.
