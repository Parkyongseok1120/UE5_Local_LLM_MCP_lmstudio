# Wrapper Mandatory Rules

## Action Request Validation Rule

액션 요청 처리 순서는 다음을 따른다.

1. 현재 상태 검사
2. 리소스 비용 검사
3. 필요한 자산/몽타주/타겟 검사
4. 실행 가능성 검사
5. 실행 성공 확인
6. 리소스 소모
7. 상태 변경
8. 이벤트 Broadcast


## Enhanced Input Rule

Enhanced Input을 사용할 때는 UInputComponent에 직접 BindAction하지 말고,
UEnhancedInputComponent로 캐스팅해서 ETriggerEvent 기반으로 바인딩한다.

예:
UEnhancedInputComponent* EnhancedInputComponent = CastChecked<UEnhancedInputComponent>(PlayerInputComponent);
EnhancedInputComponent->BindAction(MoveAction, ETriggerEvent::Triggered, this, &ThisClass::MoveInput);


## Unreal Compile Readiness Rule

코드 작성 후 반드시 다음을 검토한다.

1. .cpp에서 사용하는 모든 멤버 변수는 .h에 선언되어 있는가?
2. .cpp에서 구현한 모든 멤버 함수는 .h에 선언되어 있는가?
3. private UPROPERTY에 BlueprintReadOnly/BlueprintReadWrite를 쓸 경우 meta=(AllowPrivateAccess="true")가 있는가?
4. generated.h는 include 목록의 마지막에 있는가?
5. Build.cs에 필요한 모듈이 추가되어 있는가?
6. forward declaration과 include가 올바른가?
7. Unreal API 함수 시그니처가 현재 엔진 버전과 맞는가?
