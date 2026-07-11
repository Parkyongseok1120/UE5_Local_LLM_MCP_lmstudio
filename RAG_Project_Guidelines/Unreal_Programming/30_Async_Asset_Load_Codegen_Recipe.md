# Async Asset Load Codegen Recipe

Avoid synchronous `LoadObject` / `LoadSynchronous` in `Tick`, `BeginPlay`, or hot gameplay paths.

## Preferred pattern

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

## Latent / game-thread bounce

Use `AsyncTask(ENamedThreads::GameThread, …)` when a background callback must touch UObject state.

## Validator link

- `SYNC_LOAD_IN_GAMEPLAY`
- `HARDCODED_ASSET_PATH`
