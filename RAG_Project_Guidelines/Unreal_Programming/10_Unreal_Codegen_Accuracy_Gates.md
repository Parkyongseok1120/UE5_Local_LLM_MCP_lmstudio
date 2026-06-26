# Unreal Codegen Accuracy Gates

## Keywords

Unreal C++ code accuracy, compile-ready Unreal code, local model guardrails, constructor lifecycle, CreateDefaultSubobject, NewObject outer, TimerManager, RPC implementation, required includes, UHT, generated.h, Build.cs

Korean query aliases: Unreal code accuracy, compile-ready UE code, CreateDefaultSubobject location, NewObject Outer, TimerManager, RPC Implementation, missing include, UHT error, generated.h, Build.cs

## Purpose

Use this document when the model is asked to write Unreal C++ that should compile, not merely illustrate a design.

The goal is to reduce plausible but broken code from local 27B-class models by forcing a preflight checklist before output.

## Hard Accuracy Gates

Before returning compile-ready Unreal C++:

1. Identify the exact base class and include its direct header in the reflected header.
2. Keep `*.generated.h` as the last include in every reflected header.
3. Do not put `UCLASS`, `USTRUCT`, `UINTERFACE`, or `UENUM` declarations inside a C++ namespace.
4. If a `.cpp` defines `Class::Function`, verify the function is declared in that class header, except constructors, destructors, RPC `_Implementation`, RPC `_Validate`, BlueprintNativeEvent `_Implementation`, and local non-member helpers.
5. If a header declares `UFUNCTION(Server)`, `UFUNCTION(Client)`, or `UFUNCTION(NetMulticast)`, the `.cpp` must define `FunctionName_Implementation`.
6. Use `CreateDefaultSubobject` only in the owning class constructor.
7. Do not call `SpawnActor` from a constructor.
8. Use `NewObject<T>(Outer)` with an explicit owning object, and store retained UObject references in `UPROPERTY` or `TObjectPtr`.
9. In `UActorComponent`, use `GetWorld()->GetTimerManager()` after checking `GetWorld()`, not `GetWorldTimerManager()`.
10. If a header stores `FTimerHandle`, include `TimerManager.h`.
11. If code uses `UGameplayStatics::`, include `Kismet/GameplayStatics.h` in the `.cpp`.
12. If code uses `ConstructorHelpers::`, include `UObject/ConstructorHelpers.h` and keep the lookup in the constructor.
13. If code uses `DOREPLIFETIME`, include `Net/UnrealNetwork.h`.
14. If code exposes GameplayTag value types, include `GameplayTagContainer.h` and add `GameplayTags` to Build.cs.

## Local Model Response Rule

If any of the gates cannot be verified from the current files or RAG context, do not label the code compile-ready. Ask for the missing project file, module, asset, or build output instead.

## Build Feedback Rule

After a code edit:

1. Run static validation first.
2. Run UnrealBuildTool when available.
3. Fix the first meaningful UHT/compiler/linker error.
4. Rebuild.
5. Stop only after the build output was inspected or after a concrete blocker is reported.
