# Enhanced Input 설정과 연결

입력 연결을 누가 담당할지 하나만 정해야 합니다. PlayerController라면 `SetupInputComponent`, Pawn이라면 `SetupPlayerInputComponent`처럼 실제 제공되는 함수를 사용합니다. 컴포넌트는 소유자가 연결을 넘겨주는 구조인지 확인합니다.

`UEnhancedInputComponent`와 `ETriggerEvent`로 액션을 연결하고 `EnhancedInput` 모듈·헤더를 확인합니다. 에디터에서 Input Action과 Input Mapping Context를 만들고 올바른 로컬 플레이어의 서브시스템에 매핑을 추가해야 합니다.

입력이 안 되면 매핑 추가 여부·우선순위·선택한 플레이어·조종 중인 Pawn을 확인합니다. 같은 입력을 초기화 시점마다 중복 연결하지 않습니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
void AMyPlayerController::SetupInputComponent()
{
    Super::SetupInputComponent();
    if (UEnhancedInputComponent* EIC = Cast<UEnhancedInputComponent>(InputComponent))
    {
        EIC->BindAction(MoveAction, ETriggerEvent::Triggered, this, &AMyPlayerController::OnMove);
    }
}
```
