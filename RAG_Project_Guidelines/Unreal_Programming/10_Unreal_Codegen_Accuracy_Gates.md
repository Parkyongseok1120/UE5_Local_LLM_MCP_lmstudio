# 언리얼 코드 작성 후 검증 항목

그럴듯해 보이는 코드와 실제로 빌드한 코드를 구분해야 합니다. 현재 파일이나 엔진 선언에서 확인하지 못한 함수는 지어내지 않습니다.

- 부모 클래스 헤더를 포함하고 `*.generated.h`를 마지막 include로 둡니다.
- 언리얼 선언 매크로가 있는 자료형을 새 네임스페이스에 넣지 않습니다.
- `.cpp`의 멤버 함수와 헤더 선언을 맞춥니다. RPC와 `BlueprintNativeEvent`는 필요한 `_Implementation`을 확인합니다.
- `CreateDefaultSubobject`는 해당 클래스 생성자에서 사용합니다. 생성자에서 `SpawnActor`나 실행 중 월드 상태를 사용하지 않습니다.
- `NewObject<T>(Outer)`의 소유자와 유지할 UObject 참조의 가비지 수집 추적을 확인합니다.
- 컴포넌트 타이머는 유효한 월드의 `GetTimerManager()`를 사용하고 `FTimerHandle`에 필요한 헤더를 확인합니다.
- `UGameplayStatics`는 `Kismet/GameplayStatics.h`, `ConstructorHelpers`는 `UObject/ConstructorHelpers.h`, `DOREPLIFETIME`는 `Net/UnrealNetwork.h`를 확인합니다.
- GameplayTag 값은 `GameplayTagContainer.h`와 `GameplayTags` 모듈을 확인합니다.

보조 검사를 실행할지는 작업에 맞춰 정합니다. 빌드가 필요한 요청은 실제 UBT 결과로 확인하고 첫 유효 오류부터 수정해야 합니다. 빌드하지 않았다면 컴파일 성공이라고 표시하지 않습니다.
