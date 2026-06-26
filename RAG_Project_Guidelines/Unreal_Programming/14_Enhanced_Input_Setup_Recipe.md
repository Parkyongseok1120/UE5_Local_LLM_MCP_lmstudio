# Enhanced Input Setup Recipe (UE 5.8)

## Intent

Compile-safe Enhanced Input wiring for C++ prototypes.

## Owner

Pick **one** owner and document it:
- `APlayerController::SetupInputComponent` (common for TPS/FPS)
- or `APawn` / `UActorComponent` with `SetupPlayerInputComponent`

## Minimal C++ pattern (PlayerController)

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

## Editor assets

Create **Input Action** and **Input Mapping Context** in Content. Assign in PC defaults or load in `BeginPlay` via `UEnhancedInputLocalPlayerSubsystem`.

## Common failures

| Symptom | Check |
|---------|--------|
| No input in PIE | IMC added to subsystem? Correct mapping priority? |
| Bind in BeginPlay on PC | Move binds to `SetupInputComponent` |
| Wrong pawn | LocalPlayer → PlayerController → possessed pawn chain |

## RAG mode

`codegen` for setup, `runtime_debug` when input fails at PIE.
