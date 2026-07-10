# Replication / RPC Codegen Recipe

## Replicated properties

**Header**

```cpp
UPROPERTY(Replicated)
int32 Health;

virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;
```

**Cpp**

```cpp
void AMyActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AMyActor, Health);
}
```

## RPCs

- Declare with `UFUNCTION(Server, Reliable)` / `Client` / `NetMulticast`.
- Implement `FunctionName_Implementation` in `.cpp` (and `_Validate` when used).
- Set `bReplicates = true` on actors that replicate.

## Validator links

- `REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME`
- `RPC_IMPLEMENTATION_MISSING`
- `REPLICATION_SETUP_INCOMPLETE`
