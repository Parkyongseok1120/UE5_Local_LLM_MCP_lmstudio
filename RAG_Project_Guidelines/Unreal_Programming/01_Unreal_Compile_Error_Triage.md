# Unreal Compile Error Triage

## 검색 키워드

Unreal compile error, generated.h error, UHT, UnrealHeaderTool, Build.cs dependency, module dependency, C1083, LNK2019, unresolved external symbol, unrecognized type, UCLASS, GENERATED_BODY

## 목적

Unreal C++ 코드 생성, 컴파일 에러 수정, 디버깅 조언에서 먼저 에러 종류를 분류하고 필요한 근거를 찾게 한다. 모델은 추측으로 코드를 고치기 전에 관련 심볼, include, module dependency, reflection macro, 로그 근거를 확인해야 한다.

## Error Route

| 증상 | 우선 모드 | 먼저 확인할 RAG |
|---|---|---|
| `Cannot open include file` 또는 `C1083` | module_fix | include map, module symbol, Build.cs |
| `LNK2019`, unresolved external symbol | compile_fix | build log, module symbol, 함수 선언/정의 심볼 |
| `*.generated.h` include 오류 | reflection_fix | type symbol, include order, generated.h 위치 |
| UHT가 타입을 모른다 | reflection_fix | UCLASS/USTRUCT/UENUM/UINTERFACE 심볼, include, module dependency |
| Editor 실행 중 crash/assert/ensure | runtime_debug | build/editor log, callstack, 관련 함수 심볼 |

## Build.cs Dependency Rule

헤더에서 외부 모듈 타입을 공개 API로 노출하면 `PublicDependencyModuleNames` 후보가 된다. `.cpp` 내부 구현에서만 쓰면 보통 `PrivateDependencyModuleNames` 후보가 된다. 정확한 위치는 현재 프로젝트의 기존 `*.Build.cs` 패턴과 include 위치를 같이 확인한다.

## generated.h Rule

Unreal header에서 `*.generated.h`는 해당 헤더의 마지막 include여야 한다. `UCLASS`, `USTRUCT`, `UINTERFACE`, `UENUM`, `UFUNCTION`, `UPROPERTY`가 있는 타입은 UHT가 볼 수 있는 헤더, 매크로, include, module dependency가 모두 맞아야 한다.

## LNK2019 Rule

`LNK2019`는 선언은 보이지만 링크할 정의나 모듈이 없을 때 자주 발생한다. 먼저 함수 정의가 존재하는지 확인하고, 존재한다면 해당 정의가 들어있는 모듈이 Build.cs dependency에 포함되어 있는지 확인한다.

## UHT Type Recognition Rule

UHT 타입 인식 실패는 일반 C++ 컴파일러보다 먼저 reflection parsing 단계에서 난다. forward declaration만으로 부족한 USTRUCT/UENUM/UPROPERTY 타입, 잘못된 include 순서, 누락된 module dependency, generated.h 위치 오류를 우선 확인한다.

## 답변 형식

1. 에러 종류를 `module_fix`, `reflection_fix`, `compile_fix`, `runtime_debug` 중 하나로 분류한다.
2. 관련 로그, 심볼, 모듈, include 근거를 짧게 인용한다.
3. 최소 수정 후보를 제시한다.
4. 확실하지 않으면 "확인 필요"라고 표시하고 필요한 파일/로그를 요청한다.

## 금지

- 실제 로그 없이 에러 원인을 단정하지 않는다.
- Build.cs에 큰 모듈을 무작정 추가하지 않는다.
- generated.h보다 뒤에 include를 추가하지 않는다.
- UHT 오류를 일반 C++ 문법 오류처럼만 처리하지 않는다.
