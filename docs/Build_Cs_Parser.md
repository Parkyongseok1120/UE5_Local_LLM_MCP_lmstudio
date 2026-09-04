# Build.cs 의존성 수집과 갱신

`Build.cs`에는 해당 모듈이 어떤 다른 모듈에 의존하는지 적혀 있습니다. 검색 도구는 `scripts/parse_build_cs.py`로 이 내용을 읽습니다.

다음 형식을 처리합니다.

- `PublicDependencyModuleNames.Add("Module")`
- `AddRange(new string[] { ... })`, `AddRange(new[] { ... })`
- `PrivateDependencyModuleNames`, 헤더 검색 경로, `DynamicallyLoadedModuleNames`
- `if (Target.bBuildEditor)` 안의 의존성을 읽습니다. 조건부 항목인 `conditional_dependencies`로 구분합니다.

`Build.cs`를 고쳤거나 이를 읽는 코드를 바꿨다면 프로젝트 자료를 다시 수집해야 합니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

이 방법은 실제 프로젝트 경로까지 함께 기록하므로 이름이 같은 다른 복사본을 갱신하지 않습니다. `public`은 엔진 선언 수집용 범위이므로 프로젝트에 예전 이름만 지정하는 `collect-symbols` 예시를 쓰지 말아야 합니다.

분석기 자체를 수정한 개발자는 관련 검사를 먼저 실행하면 됩니다.

```powershell
python -m pytest tests/test_parse_build_cs.py tests/test_direct_rag_project_isolation.py -q --tb=short
```

다른 수집 명령은 [검색 자료 관리](RAG_Setup.md)에 있습니다.
