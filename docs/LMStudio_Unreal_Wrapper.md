# LM Studio Unreal Compile Wrapper

LM Studio 모델이 낸 Unreal C++ 파일 묶음을 scratch 프로젝트에 적용하고, 정적 검사와 UnrealBuildTool 빌드를 반복하는 wrapper입니다.

## 기본 흐름

1. RAG context와 mandatory rule을 포함한 프롬프트 생성
2. LM Studio 모델에 JSON 파일 묶음 요청
3. scratch Unreal 프로젝트 또는 기존 프로젝트 복사본에 파일 적용
4. attempt별 diff 생성
5. Unreal compile-readiness 정적 검사
6. UBT 실행
7. 실패 시 로그를 구조화하고 오류 유형별 RAG 재검색
8. 실패 피드백을 모델에 다시 넣고 재시도

## 새 프로젝트 프로토타입

```powershell
.\rag.ps1 wrapper `
  -Question "액션 요청 순서를 지키는 Enhanced Input ActorComponent 만들어줘" `
  -ProjectName "ScratchAction"
```

긴 한국어 요청은 파일로 주는 편이 안전합니다.

```powershell
.\rag.ps1 wrapper `
  -RequestFile ".\my_request.txt" `
  -ProjectName "ScratchAction"
```

## 기존 프로젝트 복사본 모드

`-ProjectFile`을 넘기면 기본적으로 원본을 직접 수정하지 않습니다. wrapper가 `Source`, `Config`, `Plugins/*/Source`, plugin descriptor를 run directory 아래 복사한 뒤 그 복사본에서 작업합니다.

```powershell
.\rag.ps1 wrapper `
  -Question "컴파일 오류를 고쳐줘" `
  -ProjectFile "C:\Path\To\MyGame.uproject" `
  -UbtTarget "MyGameEditor"
```

원본 프로젝트에 직접 쓰려면 명시적으로 허용해야 합니다.

```powershell
.\rag.ps1 wrapper `
  -Question "컴파일 오류를 고쳐줘" `
  -ProjectFile "C:\Path\To\MyGame.uproject" `
  -UbtTarget "MyGameEditor" `
  -AllowDirectProjectWrite
```

## 산출물

각 run은 `data\wrapper_runs\...` 아래 생성됩니다.

- `run_metadata.json`
- `initial_prompt.md`
- `attempt_N\model_response.txt`
- `attempt_N\model_response.json`
- `attempt_N\diff.patch`
- `attempt_N\static_validation.txt`
- `attempt_N\structured_errors.json`
- `attempt_N\structured_errors.md`
- `attempt_N\failure_rag_context.md`
- `attempt_N\ubt.log`
- `final_diff.patch`
- `final_answer.md`

## 정적 검사

현재 wrapper는 다음을 검사합니다.

- `generated.h`가 헤더 include 목록의 마지막인지
- reflection 매크로가 있는 헤더에 `*.generated.h`가 누락됐는지
- `UCLASS`/`USTRUCT`/`UENUM`/`UINTERFACE`가 새 C++ namespace 안에 들어갔는지
- `BlueprintNativeEvent` 선언을 같은 이름의 pure virtual 함수로 중복 선언했는지
- private `UPROPERTY(BlueprintReadOnly/BlueprintReadWrite)`에 `meta=(AllowPrivateAccess="true")`가 있는지
- 런타임 모듈 헤더/소스가 editor-only 헤더를 include하는지
- raw `UObject*`/`AActor*` 계열 멤버가 `UPROPERTY`/`TObjectPtr` 없이 GC 추적 밖에 놓였는지
- Enhanced Input 사용 시 `UEnhancedInputComponent`와 `ETriggerEvent` 기반 `BindAction`을 쓰는지
- Enhanced Input 사용 시 `EnhancedInput` 모듈이 Build.cs에 있는지
- `.cpp` 구현 함수가 대응 헤더에 선언되어 있는지
- RAG module graph의 include owner 기준으로 Build.cs dependency 누락 가능성이 있는지
- 액션 요청 함수가 상태 검사, 리소스 비용 검사, 자산/타겟 검사, 실행 가능성 검사, 성공 확인, 리소스 소모, 상태 변경, 이벤트 broadcast 순서를 갖추는지

## MCP Tool

`unreal_rag_mcp.py` tools:

- `unreal_rag_search` — hybrid FTS + symbol retrieval (default)
- `unreal_symbol_lookup` — class/API shortcut
- `unreal_rag_health` — index size/chunk/source breakdown
- `unreal_rag_rebuild_status` — whether `rag.ps1 build` is needed
- `unreal_start_compile_loop` — start wrapper as background job
- `unreal_compile_loop_status` — poll job progress/output
- `unreal_rag_capabilities` — RAG vs agent role summary
- `unreal_generate_compile_loop` — deprecated alias for background start

주의:

- MCP에서 wrapper는 **즉시 jobId를 반환**하고, 완료까지 기다리지 않습니다.
- 진행 상황은 `unreal_compile_loop_status`로 polling 하세요.
- CLI에서 blocking 실행이 필요하면 `.\rag.ps1 wrapper`를 사용하세요.
