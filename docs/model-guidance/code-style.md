# 줄 간격·들여쓰기·읽을 수 있는 코드

적용: 새 코드 작성과 수정한 주변 코드의 표현.
근거: [Epic C++ 관례](sources.md#style). Unity는 저장소의 C# 관례를 따른다.

## 작성 기준

- .editorconfig, .clang-format, 저장소 지침과 일관된 기존 코드를 먼저 확인한다.
- 여러 상태 대입과 외부 호출을 한 줄에 압축하지 않는다. 조건·반환·중첩 블록을 실행 범위에 맞게 들여쓴다.
- 가드, 계산, 상태 갱신, 후속 효과 사이에 의미 있는 빈 줄을 둔다. 매 문장 사이에 빈 줄을 넣지는 않는다.
- 코드의 역할을 드러내는 이름을 쓴다. HitResult, TraceStart, QueryParams, AmmoToTransfer처럼 읽을 때 해석이 필요한 부분을 풀어 쓴다.
- 자명한 반복 변수·짧은 수학식의 이름까지 기계적으로 늘리지 않는다.
- 단순 접근자와 여러 상태를 바꾸는 함수를 구분한다. FORCEINLINE이나 한 줄 함수가 품질·성능을 보증하지 않는다.
- 주석에는 의도, 제약, 실패 처리 이유를 적는다. 구현이 없는데 “서버 권한”, “SSOT”, “설정 완료”라고만 적지 않는다.
- 기능 수정과 함께 관계없는 파일 전체를 다시 포맷하지 않는다. 기존 파일이 일관되게 나쁘다면 별도의 정리 범위를 잡는다.

## Unreal C++ 표현 예시

아래는 줄 배치만 보이는 코드 조각이다. 발사·재장전·권한 처리가 완성된 구현은 아니다.

압축된 표현:

```cpp
LastFireTime=Now;MagazineAmmo--;SetCombatState(ECombatState::Firing);
```

동일한 연산을 읽기 쉽게 표현:

```cpp
LastFireTime = Now;
MagazineAmmo--;

SetCombatState(ECombatState::Firing);
```

Epic 관례는 새 줄의 중괄호와 단일 문장 블록에도 중괄호를 사용하는 형태다. 들여쓰기 폭과 탭·공백은 프로젝트의 명시적 규칙과 맞춘다.

```cpp
if (!CurrentWeapon)
{
	return;
}

const int32 MissingAmmo = CurrentWeapon->Stats.MagazineSize - MagazineAmmo;
```

위 조각은 중괄호와 실행 블록 들여쓰기의 예시다. 실제 멤버 이름과 가드 조건은 해당 프로젝트의 정의를 확인한다.

## Unity C# 표현 예시

```csharp
if (isReloading || magazineAmmo <= 0)
{
    return;
}

magazineAmmo--;
RefreshAmmoDisplay();
```

이 예시도 포맷만 보여준다. 실제 로직에는 채택한 권한·상태·UI 계약을 적용한다. 엔진 간에 탭·공백 규칙이나 네임스페이스 선호를 무조건 공유하지 않는다.

형식은 작성 품질의 기본 조건으로 다룬다. 새 포맷 검사 실패를 작업 진행의 추가 승인 조건으로 만들지 않는다.
