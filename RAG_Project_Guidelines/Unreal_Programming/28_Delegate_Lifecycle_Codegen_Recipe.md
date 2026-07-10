# Delegate Lifecycle Codegen Recipe

## Pattern

1. **Declare** delegate type or use dynamic multicast (`DECLARE_DYNAMIC_MULTICAST_DELEGATE_*`).
2. **Bind** in `BeginPlay` / activation (`AddDynamic`, `AddUObject`, `BindUObject`).
3. **Broadcast** with exact signature arity.
4. **Unbind** in `EndPlay` / `Deinitialize` (`RemoveDynamic`, `RemoveAll`, clear montage end delegate).

## Montage end delegate

```cpp
void UMyComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (UAnimInstance* Anim = GetAnimInstance())
    {
        Anim->Montage_SetEndDelegate(FOnMontageEnded(), ActiveMontage.Get());
    }
    Super::EndPlay(EndPlayReason);
}
```

Before rebinding the same montage, clear the previous end delegate to avoid duplicate callbacks.

## Validator links

- `DELEGATE_BIND_WITHOUT_UNBIND` — bind without teardown
- `DELEGATE_BROADCAST_SIGNATURE_MISMATCH` — wrong broadcast arity
