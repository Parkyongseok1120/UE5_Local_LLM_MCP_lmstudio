# 비동기 에셋 불러오기와 객체 수명 관리

Tick처럼 자주 실행되는 경로나 끊김에 민감한 시작 처리에서 큰 에셋을 동기로 읽는지 확인해야 합니다. 필요하면 `TSoftObjectPtr`와 비동기 로딩을 사용합니다.

완료 콜백보다 객체가 먼저 사라질 수 있으므로 약한 참조와 취소·종료 처리를 함께 둡니다. 다른 스레드의 콜백이 UObject를 다룬다면 `AsyncTask(ENamedThreads::GameThread, ...)` 등 올바른 실행 위치를 확인합니다.

`SYNC_LOAD_IN_GAMEPLAY`, `HARDCODED_ASSET_PATH`는 이 부분을 확인하라는 보조 경고입니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
UPROPERTY(EditDefaultsOnly)
TSoftObjectPtr<UTexture2D> Icon;

void UMyWidget::LoadIconAsync()
{
    if (Icon.IsNull()) return;
    FStreamableManager& Streamable = UAssetManager::GetStreamableManager();
    Streamable.RequestAsyncLoad(Icon.ToSoftObjectPath(), FStreamableDelegate::CreateUObject(this, &UMyWidget::OnIconLoaded));
}
```
