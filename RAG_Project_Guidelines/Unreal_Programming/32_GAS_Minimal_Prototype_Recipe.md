# GAS 최소 기능 구현과 검증

GAS는 능력·효과·속성 처리를 위한 언리얼 기능 묶음입니다. 처음에는 능력 하나를 부여하고 실행하는 경로부터 확인해야 합니다. 현재 프로젝트와 샘플 헤더를 읽고 큰 구조로 확장합니다.

`GameplayAbilities`, `GameplayTags`, `GameplayTasks` 의존성을 실제 사용에 맞춰 확인합니다. `UAbilitySystemComponent`와 `UAttributeSet`의 소유자·수명·네트워크 권한을 정합니다.

능력 부여는 해당 컴포넌트의 `GiveAbility(FGameplayAbilitySpec(...))`, 실행은 같은 컴포넌트의 `TryActivateAbility(SpecHandle)` 등 실제 선언을 확인합니다. 전역 `GiveAbility()`나 존재를 확인하지 않은 `UAbilitySystemGlobals` 보조 함수를 만들지 말아야 합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```csharp
PrivateDependencyModuleNames.AddRange(new[] { "GameplayAbilities", "GameplayTags", "GameplayTasks" });
```
