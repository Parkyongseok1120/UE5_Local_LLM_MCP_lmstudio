# 읽기 전용과 수정·빌드 권한

기본 `SAFE` 권한은 프로젝트를 읽고 분석하는 용도입니다. 파일 수정, 명령 실행, 언리얼 빌드는 꺼져 있습니다.

```text
ALLOW_WRITE=0
ALLOW_COMMANDS=0
ALLOW_UNREAL_BUILD=0
```

신뢰하는 프로젝트에서 AI에게 수정과 빌드를 맡기려면 설치할 때 두 옵션을 함께 지정하면 됩니다.

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

다시 읽기 전용으로 돌리려면 다음 명령을 실행하고 MCP를 재시작해야 합니다.

```powershell
python install.py --profile standard --yes
```

이 권한은 실제 파일과 프로그램 실행에 적용됩니다. AI에게 계획을 세우게 하거나 작업 순서를 정하는 설정이 아닙니다. 검색 도구가 프로젝트 소스를 수정하지는 않으며, 검색 자료 갱신은 별도 기능입니다.

`Strict`는 대화별 시작·종료를 관리하는 별도 선택 기능입니다. 수정 권한을 켰다고 자동으로 활성화되지 않습니다. 자세한 경계는 [도구 사용 규칙](LMStudio_MCP_Tool_Discipline.md)에 있습니다.
