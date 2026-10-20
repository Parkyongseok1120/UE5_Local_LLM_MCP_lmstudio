"use strict";

// Synthetic UE C++ accuracy set (plan W-A1, §5): normal (N) / defect (D) pairs
// served through a fixture read tool. No real game project is read or written.
// Case folders are neutral so a path never reveals the expected label; the
// answer key lives only in CASES. Kept outside test/ because run-tests.cjs
// runs every test/*.test.(c)js file.

const crypto = require("node:crypto");
const path = require("node:path");

const PROJECT_IDENTITY = "C:\\SyntheticAudit\\AccuracyProject.uproject";
const PROJECT_ROOT = path.win32.dirname(PROJECT_IDENTITY);

function fixtureFile(filePath, lines, kind = "source") {
  const content = lines.join("\n");
  return {
    path: filePath,
    kind,
    lines,
    content,
    sha256: crypto.createHash("sha256").update(content, "utf8").digest("hex"),
  };
}

const header = (name, base, baseInclude, body, extraIncludes = [], beforeClass = []) => [
  "#pragma once",
  "",
  "#include \"CoreMinimal.h\"",
  `#include "${baseInclude}"`,
  ...extraIncludes.map(include => `#include "${include}"`),
  `#include "${name.slice(1)}.generated.h"`,
  "",
  ...(beforeClass.length ? [...beforeClass, ""] : []),
  "UCLASS()",
  `class ${name} : public ${base}`,
  "{",
  "\tGENERATED_BODY()",
  "",
  "public:",
  ...(body.some(line => /UPROPERTY\(Replicated/u.test(line))
    ? ["\tvirtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;"] : []),
  ...body,
  "};",
];

// Shared UI component used by the hit-marker cases. Identical in each folder.
const hitFeedbackHeader = header("UHitFeedbackComponent", "UActorComponent", "Components/ActorComponent.h", [
  "\tvoid ShowHitMarker(float Damage);",
]);

// Shared presentation path. These are source excerpts, not a buildable game;
// external map/input/widget setup is specified in CaseContext.md.
const hitSetup = (folder, gameMode, pawn) => [
  fixtureFile(`${folder}/HitFeedbackComponent.cpp`, [
    '#include "HitFeedbackComponent.h"', '#include "Engine/Engine.h"',
    '#include "GameFramework/PlayerController.h"',
    'void UHitFeedbackComponent::ShowHitMarker(float Damage)', '{',
    '\tconst APlayerController* PC = Cast<APlayerController>(GetOwner());',
    '\tif (PC && PC->IsLocalController() && GEngine)',
    '\t\tGEngine->AddOnScreenDebugMessage(-1, 1.f, FColor::Green, FString::Printf(TEXT("Hit: %.0f"), Damage));', '}',
  ]),
  fixtureFile(`${folder}/HitPlayerController.h`, header('AHitPlayerController', 'APlayerController', 'GameFramework/PlayerController.h', [
    '\tAHitPlayerController();',
  ])),
  fixtureFile(`${folder}/HitPlayerController.cpp`, [
    '#include "HitPlayerController.h"', '#include "HitFeedbackComponent.h"',
    'AHitPlayerController::AHitPlayerController()', '{',
    '\tCreateDefaultSubobject<UHitFeedbackComponent>(TEXT("HitFeedback"));', '}',
  ]),
  fixtureFile(`${folder}/${gameMode}.h`, header(`A${gameMode}`, 'AGameModeBase', 'GameFramework/GameModeBase.h', [
    `\tA${gameMode}();`,
    ...(pawn ? ['\tvoid ConfirmHit(AController* Shooter, float Damage);'] : [
      '\tvirtual void BeginPlay() override;', '\tTSubclassOf<class ATurretActor> TurretClass;', '\tFTransform TurretTransform;',
    ]),
  ])),
];

const files = [
  // Pair 1 N: Client RPC on a pawn possessed by the shooting remote player.
  fixtureFile("Source/Case07/HitPawn.h", header("AHitPawn", "APawn", "GameFramework/Pawn.h", [
    "\tAHitPawn();",
    "",
    "\t/** Plays the hit confirmation on the shooter's screen. */",
    "\tUFUNCTION(Client, Reliable)",
    "\tvoid ClientConfirmHit(float Damage);",
  ])),
  fixtureFile("Source/Case07/HitPawn.cpp", [
    "#include \"HitPawn.h\"",
    "#include \"HitFeedbackComponent.h\"",
    "#include \"GameFramework/PlayerController.h\"",
    "",
    "AHitPawn::AHitPawn()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void AHitPawn::ClientConfirmHit_Implementation(float Damage)",
    "{",
    "\tif (APlayerController* PC = Cast<APlayerController>(GetController()))",
    "\t{",
    "\t\tif (UHitFeedbackComponent* Feedback = PC->FindComponentByClass<UHitFeedbackComponent>())",
    "\t\t{",
    "\t\t\tFeedback->ShowHitMarker(Damage);",
    "\t\t}",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case07/HitGameMode.cpp", [
    "#include \"HitGameMode.h\"",
    "#include \"HitPawn.h\"",
    "#include \"GameFramework/PlayerController.h\"",
    '#include "HitPlayerController.h"',
    'AHitGameMode::AHitGameMode() { PlayerControllerClass = AHitPlayerController::StaticClass(); DefaultPawnClass = AHitPawn::StaticClass(); }',
    "",
    "// Called by the server-side projectile when it damages another player.",
    "void AHitGameMode::ConfirmHit(AController* Shooter, float Damage)",
    "{",
    "\tif (APlayerController* ShooterPC = Cast<APlayerController>(Shooter))",
    "\t{",
    "\t\tif (AHitPawn* ShooterPawn = Cast<AHitPawn>(ShooterPC->GetPawn()))",
    "\t\t{",
    "\t\t\tShooterPawn->ClientConfirmHit(Damage);",
    "\t\t}",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case07/HitFeedbackComponent.h", hitFeedbackHeader),
  ...hitSetup('Source/Case07', 'HitGameMode', true),

  // Pair 1 D: same Client RPC on a server-spawned turret with no owner.
  fixtureFile("Source/Case02/TurretActor.h", header("ATurretActor", "AActor", "GameFramework/Actor.h", [
    "\tATurretActor();",
    "",
    "\tvoid FireAt(APawn* Target);",
    "",
    "\t/** Shows the hit marker on player screens when the turret lands a hit. */",
    "\tUFUNCTION(Client, Reliable)",
    "\tvoid ClientShowTurretHit(float Damage);",
  ])),
  fixtureFile("Source/Case02/TurretActor.cpp", [
    "#include \"TurretActor.h\"",
    "#include \"HitFeedbackComponent.h\"",
    "#include \"GameFramework/PlayerController.h\"",
    "#include \"Kismet/GameplayStatics.h\"",
    "",
    "ATurretActor::ATurretActor()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void ATurretActor::FireAt(APawn* Target)",
    "{",
    "\tif (!HasAuthority() || !Target)",
    "\t{",
    "\t\treturn;",
    "\t}",
    "\tconst float Damage = 12.f;",
    "\tUGameplayStatics::ApplyDamage(Target, Damage, nullptr, this, nullptr);",
    "\tClientShowTurretHit(Damage);",
    "}",
    "",
    "void ATurretActor::ClientShowTurretHit_Implementation(float Damage)",
    "{",
    "\tif (APlayerController* PC = GetWorld()->GetFirstPlayerController())",
    "\t{",
    "\t\tif (UHitFeedbackComponent* Feedback = PC->FindComponentByClass<UHitFeedbackComponent>())",
    "\t\t{",
    "\t\t\tFeedback->ShowHitMarker(Damage);",
    "\t\t}",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case02/TurretGameMode.cpp", [
    "#include \"TurretGameMode.h\"",
    "#include \"TurretActor.h\"",
    '#include "HitPlayerController.h"',
    'ATurretGameMode::ATurretGameMode() { PlayerControllerClass = AHitPlayerController::StaticClass(); TurretClass = ATurretActor::StaticClass(); }',
    "",
    "void ATurretGameMode::BeginPlay()",
    "{",
    "\tSuper::BeginPlay();",
    "\tFActorSpawnParameters Params;",
    "\tParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;",
    "\tGetWorld()->SpawnActor<ATurretActor>(TurretClass, TurretTransform, Params);",
    "}",
  ]),
  fixtureFile("Source/Case02/HitFeedbackComponent.h", hitFeedbackHeader),
  ...hitSetup('Source/Case02', 'TurretGameMode', false),

  // Pair 2 N: server calls the generated Client RPC wrapper.
  fixtureFile("Source/Case11/ReloadPawn.h", header("AReloadPawn", "APawn", "GameFramework/Pawn.h", [
    "\tAReloadPawn();",
    '\tvoid PlayReloadMontage() { ++ReloadCueCount; } // Local presentation probe observed by the test UI.',
    '\tint32 ReloadCueCount = 0;',
    "",
    "\tUFUNCTION(Server, Reliable)",
    "\tvoid ServerReload();",
    "",
    "\t/** Plays the reload sound and animation for the owning player. */",
    "\tUFUNCTION(Client, Unreliable)",
    "\tvoid ClientPlayReloadCue();",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tint32 Ammo = 0;",
  ])),
  fixtureFile("Source/Case11/ReloadPawn.cpp", [
    "#include \"ReloadPawn.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "AReloadPawn::AReloadPawn()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "// ServerReload() is called from the Reload input of the locally controlled pawn.",
    "void AReloadPawn::ServerReload_Implementation()",
    "{",
    "\tAmmo = 30;",
    "\tClientPlayReloadCue();",
    "}",
    "",
    "void AReloadPawn::ClientPlayReloadCue_Implementation()",
    "{",
    "\tPlayReloadMontage();",
    "}",
    "",
    "void AReloadPawn::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AReloadPawn, Ammo);",
    "}",
  ]),

  // Pair 2 D: server calls ClientX_Implementation directly.
  fixtureFile("Source/Case05/ReloadPawn.h", header("AReloadPawn", "APawn", "GameFramework/Pawn.h", [
    "\tAReloadPawn();",
    '\tvoid PlayReloadMontage() { ++ReloadCueCount; } // Local presentation probe observed by the test UI.',
    '\tint32 ReloadCueCount = 0;',
    "",
    "\tUFUNCTION(Server, Reliable)",
    "\tvoid ServerReload();",
    "",
    "\t/** Plays the reload sound and animation for the owning player. */",
    "\tUFUNCTION(Client, Unreliable)",
    "\tvoid ClientPlayReloadCue();",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tint32 Ammo = 0;",
  ])),
  fixtureFile("Source/Case05/ReloadPawn.cpp", [
    "#include \"ReloadPawn.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "AReloadPawn::AReloadPawn()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "// ServerReload() is called from the Reload input of the locally controlled pawn.",
    "void AReloadPawn::ServerReload_Implementation()",
    "{",
    "\tAmmo = 30;",
    "\tClientPlayReloadCue_Implementation();",
    "}",
    "",
    "void AReloadPawn::ClientPlayReloadCue_Implementation()",
    "{",
    "\tPlayReloadMontage();",
    "}",
    "",
    "void AReloadPawn::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AReloadPawn, Ammo);",
    "}",
  ]),

  // Pair 3 N: owning client calls a Server RPC on its own pawn.
  fixtureFile("Source/Case03/LeverPawn.h", header("ALeverPawn", "APawn", "GameFramework/Pawn.h", [
    "\tALeverPawn();",
    "",
    "\tUFUNCTION(Server, Reliable)",
    "\tvoid ServerInteract(ALeverActor* Lever);",
    "",
    "protected:",
    "\tvirtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;",
    "\tvoid OnInteractPressed();",
    "",
    "\tUPROPERTY()",
    "\tTObjectPtr<ALeverActor> FocusedLever;",
  ], ["LeverActor.h"])),
  fixtureFile("Source/Case03/LeverPawn.cpp", [
    "#include \"LeverPawn.h\"",
    "#include \"Components/InputComponent.h\"",
    '#include "EngineUtils.h"',
    "",
    "ALeverPawn::ALeverPawn()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void ALeverPawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)",
    "{",
    "\tSuper::SetupPlayerInputComponent(PlayerInputComponent);",
    "\tPlayerInputComponent->BindAction(TEXT(\"Interact\"), IE_Pressed, this, &ALeverPawn::OnInteractPressed);",
    "}",
    "",
    "void ALeverPawn::OnInteractPressed()",
    "{",
    '\tFocusedLever = nullptr;',
    '\tfor (TActorIterator<ALeverActor> It(GetWorld()); It; ++It) { FocusedLever = *It; break; }',
    "\tif (FocusedLever)",
    "\t{",
    "\t\tServerInteract(FocusedLever);",
    "\t}",
    "}",
    "",
    "void ALeverPawn::ServerInteract_Implementation(ALeverActor* Lever)",
    "{",
    "\tif (Lever)",
    "\t{",
    "\t\tLever->Toggle();",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case03/LeverActor.h", header("ALeverActor", "AActor", "GameFramework/Actor.h", [
    "\tALeverActor();",
    "",
    "\t/** Placed in the level. Server-side state change. */",
    "\tvoid Toggle();",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tbool bOpen = false;",
  ])),

  // Pair 3 D: client calls a Server RPC on a level-placed actor it does not own.
  fixtureFile('Source/Case03/LeverActor.cpp', [
    '#include "LeverActor.h"', '#include "Net/UnrealNetwork.h"',
    'ALeverActor::ALeverActor() { bReplicates = true; }',
    'void ALeverActor::Toggle() { bOpen = !bOpen; }',
    'void ALeverActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const',
    '{', '\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);', '\tDOREPLIFETIME(ALeverActor, bOpen);', '}',
  ]),
  fixtureFile("Source/Case09/LeverPawn.h", header("ALeverPawn", "APawn", "GameFramework/Pawn.h", [
    "\tALeverPawn();",
    "",
    "protected:",
    "\tvirtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;",
    "\tvoid OnInteractPressed();",
    "",
    "\tUPROPERTY()",
    "\tTObjectPtr<ALeverActor> FocusedLever;",
  ], ["LeverActor.h"])),
  fixtureFile("Source/Case09/LeverPawn.cpp", [
    "#include \"LeverPawn.h\"",
    "#include \"Components/InputComponent.h\"",
    '#include "EngineUtils.h"',
    "",
    "ALeverPawn::ALeverPawn()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void ALeverPawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)",
    "{",
    "\tSuper::SetupPlayerInputComponent(PlayerInputComponent);",
    "\tPlayerInputComponent->BindAction(TEXT(\"Interact\"), IE_Pressed, this, &ALeverPawn::OnInteractPressed);",
    "}",
    "",
    "void ALeverPawn::OnInteractPressed()",
    "{",
    '\tFocusedLever = nullptr;',
    '\tfor (TActorIterator<ALeverActor> It(GetWorld()); It; ++It) { FocusedLever = *It; break; }',
    "\tif (FocusedLever)",
    "\t{",
    "\t\tFocusedLever->ServerToggle();",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case09/LeverActor.h", header("ALeverActor", "AActor", "GameFramework/Actor.h", [
    "\tALeverActor();",
    "",
    "\t/** Placed in the level. Server-side state change. */",
    "\tUFUNCTION(Server, Reliable)",
    "\tvoid ServerToggle();",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tbool bOpen = false;",
  ])),
  fixtureFile("Source/Case09/LeverActor.cpp", [
    "#include \"LeverActor.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "ALeverActor::ALeverActor()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void ALeverActor::ServerToggle_Implementation()",
    "{",
    "\tbOpen = !bOpen;",
    "}",
    "",
    "void ALeverActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(ALeverActor, bOpen);",
    "}",
  ]),

  // Pair 4 N: two counters share one empty RepNotify; UI polls, no notify consumer.
  fixtureFile("Source/Case12/StatsPlayerState.h", header("AStatsPlayerState", "APlayerState", "GameFramework/PlayerState.h", [
    "\tUPROPERTY(ReplicatedUsing = OnRep_KillCount)",
    "\tint32 KillCount = 0;",
    "",
    "\tUPROPERTY(ReplicatedUsing = OnRep_KillCount)",
    "\tint32 DeathCount = 0;",
    "",
    "protected:",
    "\tUFUNCTION()",
    "\tvoid OnRep_KillCount();",
  ])),
  fixtureFile("Source/Case12/StatsPlayerState.cpp", [
    "#include \"StatsPlayerState.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "void AStatsPlayerState::OnRep_KillCount()",
    "{",
    "}",
    "",
    "void AStatsPlayerState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AStatsPlayerState, KillCount);",
    "\tDOREPLIFETIME(AStatsPlayerState, DeathCount);",
    "}",
  ]),
  fixtureFile("Source/Case12/StatsWidget.cpp", [
    "#include \"StatsWidget.h\"",
    "#include \"StatsPlayerState.h\"",
    "#include \"Components/TextBlock.h\"",
    "",
    "void UStatsWidget::NativeTick(const FGeometry& MyGeometry, float InDeltaTime)",
    "{",
    "\tSuper::NativeTick(MyGeometry, InDeltaTime);",
    "\tif (const AStatsPlayerState* PS = GetOwningPlayerState<AStatsPlayerState>())",
    "\t{",
    "\t\tStatsText->SetText(FText::Format(NSLOCTEXT(\"Stats\", \"KD\", \"{0} / {1}\"), PS->KillCount, PS->DeathCount));",
    "\t}",
    "}",
  ]),

  // Pair 4 D: RepNotify body reacts to the wrong field; event-driven UI misses health.
  fixtureFile("Source/Case01/VitalsCharacter.h", header("AVitalsCharacter", "ACharacter", "GameFramework/Character.h", [
    "\tUPROPERTY(BlueprintAssignable)",
    "\tFOnVitalChanged OnHealthChanged;",
    "",
    "\tUPROPERTY(BlueprintAssignable)",
    "\tFOnVitalChanged OnArmorChanged;",
    "",
    "\tvirtual float TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser) override;",
    "",
    "protected:",
    "\tUPROPERTY(ReplicatedUsing = OnRep_Health)",
    "\tfloat Health = 100.f;",
    "",
    "\tUPROPERTY(Replicated)",
    "\tfloat Armor = 50.f;",
    "",
    "\tUFUNCTION()",
    "\tvoid OnRep_Health();",
  ], [], ["DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnVitalChanged, float, NewValue);"])),
  fixtureFile("Source/Case01/VitalsCharacter.cpp", [
    "#include \"VitalsCharacter.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "float AVitalsCharacter::TakeDamage(float DamageAmount, FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser)",
    "{",
    "\tconst float Applied = Super::TakeDamage(DamageAmount, DamageEvent, EventInstigator, DamageCauser);",
    "\tHealth = FMath::Max(0.f, Health - Applied);",
    "\tOnHealthChanged.Broadcast(Health);",
    "\treturn Applied;",
    "}",
    "",
    "void AVitalsCharacter::OnRep_Health()",
    "{",
    "\tOnArmorChanged.Broadcast(Armor);",
    "}",
    "",
    "void AVitalsCharacter::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AVitalsCharacter, Health);",
    "\tDOREPLIFETIME(AVitalsCharacter, Armor);",
    "}",
  ]),
  fixtureFile("Source/Case01/HealthBarWidget.cpp", [
    "#include \"HealthBarWidget.h\"",
    "#include \"VitalsCharacter.h\"",
    "#include \"Components/ProgressBar.h\"",
    "",
    "void UHealthBarWidget::BindTo(AVitalsCharacter* Character)",
    "{",
    "\tif (Character)",
    "\t{",
    "\t\tCharacter->OnHealthChanged.AddDynamic(this, &UHealthBarWidget::HandleHealthChanged);",
    "\t}",
    "}",
    "",
    "void UHealthBarWidget::HandleHealthChanged(float NewHealth)",
    "{",
    "\tHealthBar->SetPercent(NewHealth / 100.f);",
    "}",
  ]),

  // Pair 5 N: one server-only writer, replicated copy read by clients.
  fixtureFile("Source/Case06/ScorePlayerState.h", header("AScorePlayerState", "APlayerState", "GameFramework/PlayerState.h", [
    "\tvoid AddKill();",
    "\tint32 GetKills() const { return Kills; }",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tint32 Kills = 0;",
  ])),
  fixtureFile("Source/Case06/ScorePlayerState.cpp", [
    "#include \"ScorePlayerState.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "void AScorePlayerState::AddKill()",
    "{",
    "\tcheck(HasAuthority());",
    "\t++Kills;",
    "}",
    "",
    "void AScorePlayerState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AScorePlayerState, Kills);",
    "}",
  ]),
  fixtureFile("Source/Case06/DuelGameMode.cpp", [
    "#include \"DuelGameMode.h\"",
    "#include \"ScorePlayerState.h\"",
    "",
    "void ADuelGameMode::OnPlayerKilled(AController* Killer, AController* Victim)",
    "{",
    "\tif (AScorePlayerState* PS = Killer ? Killer->GetPlayerState<AScorePlayerState>() : nullptr)",
    "\t{",
    "\t\tPS->AddKill();",
    "\t}",
    "}",
  ]),

  // Pair 5 D: the same counter is also written on the owning client.
  fixtureFile("Source/Case13/ScorePlayerState.h", header("AScorePlayerState", "APlayerState", "GameFramework/PlayerState.h", [
    "\tvoid AddKill();",
    "\tint32 GetKills() const { return Kills; }",
    "",
    "\tUPROPERTY(Replicated)",
    "\tint32 Kills = 0;",
  ])),
  fixtureFile("Source/Case13/ScorePlayerState.cpp", [
    "#include \"ScorePlayerState.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "void AScorePlayerState::AddKill()",
    "{",
    "\t++Kills;",
    "}",
    "",
    "void AScorePlayerState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AScorePlayerState, Kills);",
    "}",
  ]),
  fixtureFile("Source/Case13/DuelGameMode.cpp", [
    "#include \"DuelGameMode.h\"",
    "#include \"DuelCharacter.h\"",
    "#include \"ScorePlayerState.h\"",
    "",
    "void ADuelGameMode::OnPlayerKilled(AController* Killer, AController* Victim)",
    "{",
    "\tif (AScorePlayerState* PS = Killer ? Killer->GetPlayerState<AScorePlayerState>() : nullptr)",
    "\t{",
    "\t\tPS->AddKill();",
    "\t}",
    "\tif (ADuelCharacter* KillerCharacter = Killer ? Cast<ADuelCharacter>(Killer->GetPawn()) : nullptr)",
    "\t{",
    "\t\tKillerCharacter->ClientNotifyKill();",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case13/DuelCharacter.cpp", [
    "#include \"DuelCharacter.h\"",
    "#include \"ScorePlayerState.h\"",
    "",
    "// UFUNCTION(Client, Reliable) void ClientNotifyKill(); in DuelCharacter.h",
    "void ADuelCharacter::ClientNotifyKill_Implementation()",
    "{",
    "\tif (AScorePlayerState* PS = GetPlayerState<AScorePlayerState>())",
    "\t{",
    "\t\tPS->AddKill();",
    "\t}",
    "\tPlayKillConfirmSound();",
    "}",
  ]),

  // Pair 6 N: project-defined Health and Die().
  fixtureFile("Source/Case10/ArenaCharacter.h", header("AArenaCharacter", "ACharacter", "GameFramework/Character.h", [
    "\tvirtual float TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser) override;",
    "",
    "protected:",
    "\tvoid Die();",
    "",
    "\tUPROPERTY(Replicated)",
    "\tfloat Health = 100.f;",
  ])),
  fixtureFile("Source/Case10/ArenaCharacter.cpp", [
    "#include \"ArenaCharacter.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "float AArenaCharacter::TakeDamage(float DamageAmount, FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser)",
    "{",
    "\tconst float Applied = Super::TakeDamage(DamageAmount, DamageEvent, EventInstigator, DamageCauser);",
    "\tif (!HasAuthority() || Health <= 0.f)",
    "\t{",
    "\t\treturn Applied;",
    "\t}",
    "\tHealth = FMath::Max(0.f, Health - Applied);",
    "\tif (Health <= 0.f)",
    "\t{",
    "\t\tDie();",
    "\t}",
    "\treturn Applied;",
    "}",
    "",
    "void AArenaCharacter::Die()",
    "{",
    "\tDetachFromControllerPendingDestroy();",
    "\tSetLifeSpan(3.f);",
    "}",
    "",
    "void AArenaCharacter::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(AArenaCharacter, Health);",
    "}",
  ]),
  fixtureFile("Source/Case10/ArenaProjectile.cpp", [
    "#include \"ArenaProjectile.h\"",
    "#include \"Kismet/GameplayStatics.h\"",
    "",
    "void AArenaProjectile::OnHit(UPrimitiveComponent* HitComp, AActor* OtherActor, UPrimitiveComponent* OtherComp, FVector NormalImpulse, const FHitResult& Hit)",
    "{",
    "\tif (HasAuthority() && OtherActor && OtherActor != GetOwner())",
    "\t{",
    "\t\tUGameplayStatics::ApplyPointDamage(OtherActor, 40.f, GetVelocity().GetSafeNormal(), Hit, GetInstigatorController(), this, nullptr);",
    "\t}",
    "\tDestroy();",
    "}",
  ]),

  // Pair 6 D: no project Health/Die; the design note relies on engine defaults.
  fixtureFile("Source/Case04/BarrelActor.h", header("ABarrelActor", "AActor", "GameFramework/Actor.h", [
    "\tABarrelActor();",
    "",
    "\t/** Explosive barrel. Destroyed after it has taken 100 damage in total. */",
    "\tUPROPERTY(EditAnywhere)",
    "\tTObjectPtr<UStaticMeshComponent> Mesh;",
  ])),
  fixtureFile("Source/Case04/BarrelActor.cpp", [
    "#include \"BarrelActor.h\"",
    "#include \"Components/StaticMeshComponent.h\"",
    "",
    "ABarrelActor::ABarrelActor()",
    "{",
    "\tbReplicates = true;",
    "\tMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT(\"Mesh\"));",
    "\tRootComponent = Mesh;",
    "\tSetCanBeDamaged(true);",
    "}",
  ]),
  fixtureFile("Source/Case04/ArenaProjectile.cpp", [
    "#include \"ArenaProjectile.h\"",
    "#include \"Kismet/GameplayStatics.h\"",
    "",
    "void AArenaProjectile::OnHit(UPrimitiveComponent* HitComp, AActor* OtherActor, UPrimitiveComponent* OtherComp, FVector NormalImpulse, const FHitResult& Hit)",
    "{",
    "\tif (HasAuthority() && OtherActor && OtherActor != GetOwner())",
    "\t{",
    "\t\tUGameplayStatics::ApplyPointDamage(OtherActor, 40.f, GetVelocity().GetSafeNormal(), Hit, GetInstigatorController(), this, nullptr);",
    "\t}",
    "\tDestroy();",
    "}",
  ]),

  // Pair 7: a prior finding with a structure-only check, then contrary source evidence.
  fixtureFile("Source/Case08/RelayBeacon.h", header("ARelayBeacon", "AActor", "GameFramework/Actor.h", [
    "\tARelayBeacon();",
    "",
    "\tUFUNCTION(Server, Reliable)",
    "\tvoid ServerActivate();",
    "",
    "protected:",
    "\tUPROPERTY(Replicated)",
    "\tbool bActive = false;",
  ])),
  fixtureFile("Source/Case08/RelayBeacon.cpp", [
    "#include \"RelayBeacon.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "ARelayBeacon::ARelayBeacon()",
    "{",
    "\tbReplicates = true;",
    "}",
    "",
    "void ARelayBeacon::ServerActivate_Implementation()",
    "{",
    "\tbActive = true;",
    "}",
    "",
    "void ARelayBeacon::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(ARelayBeacon, bActive);",
    "}",
  ]),
  fixtureFile("Source/Case08/RelayGameMode.cpp", [
    "#include \"RelayGameMode.h\"",
    "#include \"RelayBeacon.h\"",
    "#include \"RelayPlayerController.h\"",
    "",
    "void ARelayGameMode::PostLogin(APlayerController* NewPlayer)",
    "{",
    "\tSuper::PostLogin(NewPlayer);",
    "\tFActorSpawnParameters Params;",
    "\tParams.Owner = NewPlayer;",
    "\tif (ARelayBeacon* Beacon = GetWorld()->SpawnActor<ARelayBeacon>(BeaconClass, BeaconTransform, Params))",
    "\t{",
    "\t\tCastChecked<ARelayPlayerController>(NewPlayer)->SetBeacon(Beacon);",
    "\t}",
    "}",
  ]),
  fixtureFile("Source/Case08/RelayPlayerController.cpp", [
    "#include \"RelayPlayerController.h\"",
    "#include \"RelayBeacon.h\"",
    "#include \"Net/UnrealNetwork.h\"",
    "",
    "// Beacon is UPROPERTY(Replicated) TObjectPtr<ARelayBeacon> in RelayPlayerController.h.",
    "void ARelayPlayerController::OnActivatePressed()",
    "{",
    "\tif (Beacon)",
    "\t{",
    "\t\tBeacon->ServerActivate();",
    "\t}",
    "}",
    "",
    "void ARelayPlayerController::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const",
    "{",
    "\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);",
    "\tDOREPLIFETIME(ARelayPlayerController, Beacon);",
    "}",
  ]),
  fixtureFile("Evidence/Case08-ContractCheck.json", JSON.stringify({
    finding: "F-1",
    severity: "P1",
    claim: "RelayPlayerController calls ServerActivate on ARelayBeacon, which is not owned by the calling client, so the RPC is dropped.",
    check: "unreal_rpc_contract",
    status: "structure_valid",
    facts: "facts_not_verified",
    note: "Declaration and _Implementation pairing are well formed. Ownership was not evaluated.",
  }, null, 2).split("\n"), "evidence"),
];

const SETUP = {
  Case07: 'The map uses AHitGameMode. Each remote player possesses its spawned AHitPawn. The server projectile calls ConfirmHit with its shooter controller. On-screen debug messages are enabled.',
  Case02: 'The map uses ATurretGameMode. FireAt is invoked on the server after a target is acquired. The hit marker is intended for remote player screens. On-screen debug messages are enabled.',
  Case11: 'Each remote player possesses AReloadPawn; Reload input invokes ServerReload on that pawn. The local UI observes ReloadCueCount. Assess delivery routing assuming the unreliable packet arrives.',
  Case05: 'Each remote player possesses AReloadPawn; Reload input invokes ServerReload on that pawn. The local UI observes ReloadCueCount. Assess delivery routing assuming the unreliable packet arrives.',
  Case03: 'The map contains exactly one replicated ALeverActor, with Owner unset on server and clients. Each remote player possesses ALeverPawn. Interact input is mapped and pressed after the lever replicates. Assess RPC routing, not range/anti-cheat policy.',
  Case09: 'The map contains exactly one replicated ALeverActor, with Owner unset on server and clients. Each remote player possesses ALeverPawn. Interact input is mapped and pressed after the lever replicates. Assess RPC routing, not range/anti-cheat policy.',
  Case12: 'The UI exists on the owning remote client and its widget controls are bound. Replicated values change on the server after the widget is ready. Widget tick is enabled.',
  Case01: 'The UI exists on the owning remote client and its widget controls are bound. Replicated values change on the server after the widget is ready. BindTo is called once with the replicated character.',
  Case06: 'The server game mode receives OnPlayerKilled once per kill. Each controller possesses its player character and uses AScorePlayerState. Remote UI reads GetKills. Begin with Kills=0 on both sides.',
  Case13: 'The server game mode receives OnPlayerKilled once per kill. Each controller possesses its player character and uses AScorePlayerState. Remote UI reads GetKills. Begin with Kills=0 on both sides.',
  Case10: 'Projectile OnHit is bound to collision and executes on the server. TakeDamage receives positive damage. The project excerpt shows all target damage/death handling; no Blueprint damage handlers are added.',
  Case04: 'Projectile OnHit is bound to collision and executes on the server. TakeDamage receives positive damage. The project excerpt shows all target damage/death handling; no Blueprint damage handlers are added.',
  Case08: 'The map uses ARelayGameMode and ARelayPlayerController. SetBeacon assigns the replicated Beacon member on the server. OnActivatePressed is bound to local input and pressed after that reference replicates.',
};
for (const [folder, setup] of Object.entries(SETUP)) files.push(fixtureFile(`Source/${folder}/CaseContext.md`, [
  'Source-level scenario, not a standalone build target. Engine-generated declarations and unrelated presentation/assets are omitted.',
  'External integration preconditions for this scenario:', setup,
], 'scenario'));

// Additional consumer/replication probes. Neutral folders and identical prompts
// keep labels out of the model input; renamed copies are held out for evaluation.
const consumerCases = [
  { id: 'P9-N', folder: 'Source/Case14', pawn: 'SignalPawn', hud: 'SignalHUD', method: 'ClientPulse', getter: 'GetPulseTime', value: 'PulseTime', draw: true },
  { id: 'P9-D', folder: 'Source/Case17', pawn: 'SignalPawn', hud: 'SignalHUD', method: 'ClientPulse', getter: 'GetPulseTime', value: 'PulseTime', draw: false },
  { id: 'H9-N', folder: 'Source/Case22', pawn: 'EchoPawn', hud: 'EchoOverlay', method: 'ClientAcknowledge', getter: 'GetAcknowledgedAt', value: 'AcknowledgedAt', draw: true, heldout: true },
  { id: 'H9-D', folder: 'Source/Case19', pawn: 'EchoPawn', hud: 'EchoOverlay', method: 'ClientAcknowledge', getter: 'GetAcknowledgedAt', value: 'AcknowledgedAt', draw: false, heldout: true },
];
for (const item of consumerCases) {
  const { folder, pawn, hud, method, getter, value, draw } = item;
  const batch = [
    fixtureFile(`${folder}/${pawn}.h`, header(`A${pawn}`, 'APawn', 'GameFramework/Pawn.h', [
      `\tA${pawn}();`, '\tUFUNCTION(Client, Reliable)', `\tvoid ${method}();`,
      `\tdouble ${getter}() const { return ${value}; }`, 'private:', `\tdouble ${value} = -1000.0;`,
    ])),
    fixtureFile(`${folder}/${pawn}.cpp`, [
      `#include "${pawn}.h"`, '#include "Engine/World.h"',
      `A${pawn}::A${pawn}() { bReplicates = true; }`,
      `void A${pawn}::${method}_Implementation()`, '{', `\t${value} = GetWorld()->GetTimeSeconds();`, '}',
    ]),
    fixtureFile(`${folder}/${hud}.h`, header(`A${hud}`, 'AHUD', 'GameFramework/HUD.h', ['\tvirtual void DrawHUD() override;'])),
    fixtureFile(`${folder}/${hud}.cpp`, [
      `#include "${hud}.h"`, `#include "${pawn}.h"`, '#include "Engine/World.h"',
      `void A${hud}::DrawHUD()`, '{', '\tSuper::DrawHUD();',
      `\tconst A${pawn}* Pawn = Cast<A${pawn}>(GetOwningPawn());`,
      ...(draw ? [`\tif (Pawn && GetWorld()->GetTimeSeconds() - Pawn->${getter}() < 0.15)`,
        '\t\tDrawRect(FLinearColor::Red, 200.f, 200.f, 8.f, 8.f);'] : ['\t(void)Pawn;']), '}',
    ]),
    fixtureFile(`${folder}/MatchMode.cpp`, [
      '#include "MatchMode.h"', `#include "${pawn}.h"`,
      'void AMatchMode::ConfirmFor(AController* Shooter)', '{',
      `\tif (A${pawn}* Pawn = Cast<A${pawn}>(Shooter->GetPawn()))`, `\t\tPawn->${method}();`, '}',
    ]),
    fixtureFile(`${folder}/MatchMode.h`, header('AMatchMode', 'AGameModeBase', 'GameFramework/GameModeBase.h', ['\tvoid ConfirmFor(AController* Shooter);'])),
    fixtureFile(`${folder}/CaseContext.md`, [
      `The active map uses AMatchMode. Remote players possess A${pawn}, and their local HUD class is A${hud}.`,
      'ConfirmFor is called on the server for each validated hit, with the shooting player controller.',
      'Requirement: the shooting player sees a red rectangle for 0.15 seconds after confirmation.',
      'The HUD ticks with a valid Canvas. No Blueprint or external delegate adds another presentation path.',
      'These excerpts contain the complete confirmation-to-HUD path. This is not a complete engine implementation.',
    ], 'scenario'),
  ];
  files.push(...(item.heldout ? batch.reverse() : batch));
}
const replicationCases = [
  { id: 'P10-N', folder: 'Source/Case16', condition: 'COND_OwnerOnly', variant: 'N' },
  { id: 'P10-D', folder: 'Source/Case21', condition: 'COND_SkipOwner', variant: 'D' },
];
for (const { folder, condition } of replicationCases) files.push(
  fixtureFile(`${folder}/SupplyPawn.h`, header('ASupplyPawn', 'APawn', 'GameFramework/Pawn.h', [
    '\tASupplyPawn();', '\tvoid SpendOne();', '\tUPROPERTY(Replicated)', '\tint32 Supply = 30;',
  ])),
  fixtureFile(`${folder}/SupplyPawn.cpp`, [
    '#include "SupplyPawn.h"', '#include "Net/UnrealNetwork.h"',
    'ASupplyPawn::ASupplyPawn() { bReplicates = true; }',
    'void ASupplyPawn::SpendOne() { if (HasAuthority() && Supply > 0) --Supply; }',
    'void ASupplyPawn::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const', '{',
    '\tSuper::GetLifetimeReplicatedProps(OutLifetimeProps);',
    `\tDOREPLIFETIME_CONDITION(ASupplyPawn, Supply, ${condition});`, '}',
  ]),
  fixtureFile(`${folder}/SupplyHUD.h`, header('ASupplyHUD', 'AHUD', 'GameFramework/HUD.h', ['\tvirtual void DrawHUD() override;'])),
  fixtureFile(`${folder}/SupplyHUD.cpp`, [
    '#include "SupplyHUD.h"', '#include "SupplyPawn.h"', 'void ASupplyHUD::DrawHUD()', '{', '\tSuper::DrawHUD();',
    '\tif (const ASupplyPawn* Pawn = Cast<ASupplyPawn>(GetOwningPawn()))',
    '\t\tDrawText(FString::FromInt(Pawn->Supply), FLinearColor::White, 100.f, 100.f);', '}',
  ]),
  fixtureFile(`${folder}/CaseContext.md`, [
    'Each remote player possesses a replicated ASupplyPawn. The local ASupplyHUD ticks with a valid Canvas.',
    'The server calls SpendOne for accepted shots. There are no other writes or alternate update messages.',
    'Requirement: only the owning player needs this counter, and after delivery it matches the server count.',
    'Evaluate a connected remote player after the server accepts a shot. No dormancy, packet loss or custom replication overrides.',
  ], 'scenario'),
);

const FILES = new Map(files.map(file => [file.path, file]));

function readPayload(filePath, startLine = 1, endLine = Number.MAX_SAFE_INTEGER) {
  const normalizedPath = String(filePath || "").replace(/^project:\/\//iu, "");
  const file = FILES.get(normalizedPath);
  if (!file) return { ok: false, errorCode: "NOT_FOUND", message: `Synthetic file not found: ${filePath}` };
  const start = Math.max(1, Math.min(file.lines.length, Math.trunc(Number(startLine) || 1)));
  const requestedEnd = Math.max(start, Math.trunc(Number(endLine) || file.lines.length));
  const end = Math.min(file.lines.length, requestedEnd, start + 219);
  return {
    ok: true,
    kind: "workspace_file_observation",
    projectIdentity: PROJECT_IDENTITY,
    path: `project://${file.path}`,
    resolvedRootType: "active_project",
    projectRelativePath: file.path,
    workspaceRelativePath: file.path,
    absolutePath: path.win32.join(PROJECT_ROOT, ...file.path.split("/")),
    activeProject: PROJECT_IDENTITY,
    displayPath: `project://${file.path}`,
    sha256: file.sha256,
    startLine: start,
    endLine: end,
    totalLines: file.lines.length,
    // A requested range is complete even when the file contains later lines.
    truncated: end < Math.min(file.lines.length, requestedEnd),
    ...(end < Math.min(file.lines.length, requestedEnd) ? { nextStartLine: end + 1 } : {}),
    content: file.lines.slice(start - 1, end).join("\n"),
  };
}

// Evidence read from installed UE 5.8.2 on 2026-10-19; the runner verifies hashes.
const ENGINE_BUILD = Object.freeze({ version: "5.8.2", changelist: 56702186 });
const SOURCES = Object.freeze({
  replicatedDeclaration: Object.freeze({ kind: 'engine_source', engine: ENGINE_BUILD,
    path: 'Engine/Source/Runtime/Engine/Public/Net/UnrealNetwork.h', symbol: 'GetReplicatedProperty', lines: [217, 229],
    sha256: '3637771a600a20b0c5f83f3f0cbce7aa60c39a32765f188651f5a1bc8302798e',
    statement: 'Lifetime registration requires CPF_Net, supplied by Replicated or ReplicatedUsing.' }),
  lifetimeCondition: Object.freeze({ kind: 'engine_source', engine: ENGINE_BUILD,
    path: 'Engine/Source/Runtime/CoreUObject/Public/UObject/CoreNetTypes.h', symbol: 'ELifetimeCondition', lines: [14, 35],
    sha256: '8a467081f1ed3d0cb25f6e4e6080703de70f910d2a119b939a349a245d747778',
    statement: 'OwnerOnly and SkipOwner select different receiving connections; declaration and delivery condition are separate.' }),
  receiveOwnership: Object.freeze({ kind: 'engine_source', engine: ENGINE_BUILD,
    path: 'Engine/Source/Runtime/Engine/Private/DataChannel.cpp', symbol: 'UActorChannel::ProcessBunch', lines: [3389, 3397],
    sha256: '4e1be22679a16d235396a836befa0ef46dcc410ab1af4848467f9cf7c90776dd',
    statement: 'bNetOwner is established from the actor owning connection matching the incoming connection (including child connections).' }),
  receiveAuthorization: Object.freeze({ kind: 'engine_source', engine: ENGINE_BUILD,
    path: 'Engine/Source/Runtime/Engine/Private/NetDriver.cpp', symbol: 'UNetDriver::ShouldCallRemoteFunction', lines: [8452, 8455],
    sha256: '3e611778a8742277af0a14f27af8f63d5a3d54e2a08af6db4726b4520cc2a728',
    statement: 'The base driver only allows server-side execution when RepFlags.bNetOwner is true and RPCs are not ignored.' }),
  receiveDispatch: Object.freeze({ kind: 'engine_source', engine: ENGINE_BUILD,
    path: 'Engine/Source/Runtime/Engine/Private/DataReplication.cpp', symbol: 'FObjectReplicator::ReceivedRPC', lines: [1387, 1476],
    sha256: '2ff12da150ce1aab587fe6eb94f58f3ba717293994074bf67f474862cea03b70',
    statement: 'ReceivedRPC asks ShouldCallRemoteFunction before dispatching ProcessEvent; sending remotely alone does not prove execution.' }),
  rpcTable: Object.freeze({ kind: "engine_source", engine: ENGINE_BUILD, path: "Engine/Source/Runtime/Engine/Private/Actor.cpp", symbol: "AActor::GetFunctionCallspace", lines: [5467,5665], sha256: "53022fa18c2e33a389fdfa8cc49e63f2795eef648fb755157f78625965e6f329", statement: "RPC routing depends on flags, call direction and owning connection; the wrapper dispatches through callspace." }),
  propertyReplication: Object.freeze({ kind: "engine_source", engine: ENGINE_BUILD, path: "Engine/Source/Runtime/Engine/Private/RepLayout.cpp", symbol: "FRepLayout::CallRepNotifies", lines: [4661,4705], sha256: "0efd440a938c7f1b8fa075a55344ad8fafb0a6309fc66530e36e2f7a7250a472", statement: "The receiving replication state selects the property RepNotify and invokes it through ProcessEvent." }),
  callspace: Object.freeze({ kind: "engine_source", engine: ENGINE_BUILD, path: "Engine/Source/Runtime/Engine/Private/Actor.cpp", symbol: "AActor::GetFunctionCallspace", lines: [5467,5665], sha256: "53022fa18c2e33a389fdfa8cc49e63f2795eef648fb755157f78625965e6f329", statement: "An authority actor without an owning player can execute a Client RPC locally (5627); a valid owning connection routes remotely (5664)." }),
  noOwningConnection: Object.freeze({ kind: "engine_source", engine: ENGINE_BUILD, path: "Engine/Source/Runtime/Engine/Private/NetDriver.cpp", symbol: "UNetDriver::ProcessRemoteFunction", lines: [8259,8278], sha256: "3e611778a8742277af0a14f27af8f63d5a3d54e2a08af6db4726b4520cc2a728", statement: "Remote dispatch without an owning connection logs a warning and does not process the function." }),
  pointDamage: Object.freeze({ kind: "engine_source", engine: ENGINE_BUILD, path: "Engine/Source/Runtime/Engine/Private/Actor.cpp", symbol: "AActor::TakeDamage", lines: [3417,3514], sha256: "53022fa18c2e33a389fdfa8cc49e63f2795eef648fb755157f78625965e6f329", statement: "InternalTakePointDamage returns damage unchanged; AActor::TakeDamage keeps no health and destroys nothing." }),
  rpcDeclarationGuidance: Object.freeze({ kind: "guidance", path: "docs/model-guidance/unreal.md", statement: "RPC X is declared in the header; X_Implementation defines the body. Direct calls bypass the wrapper." }),
});

const ref = (file, anchor) => ({ kind: "fixture_source", path: file, anchor });

const LABELS = Object.freeze(["defect", "not_defect", "needs_runtime_proof"]);
const SEVERITIES = Object.freeze(["P0", "P1", "P2", "P3"]);
const RUNTIME = "Runtime: dedicated server with remote clients, standard UNetDriver legacy replication (Iris disabled), no custom RPC authorization override.";
const QUESTION = (folder, focus) => [
  `Audit ${folder} of the active Unreal project. ${RUNTIME}`,
  `Read ${folder}/CaseContext.md for the scenario's explicit integration preconditions.`,
  `Question: ${focus}`,
  `Answer with one label (${LABELS.join(" | ")}), a severity (${SEVERITIES.join(" | ")}) or none, and the exact source lines that decide it.`,
].join(" ");

// One focus string per pair: N and D receive the same question.
const FOCUS = Object.freeze({
  1: "Does the hit marker appear on the screen of the player it is meant for?",
  2: "Does the reload cue play for the player who reloaded?",
  3: "Does pressing Interact on a remote client toggle the lever on the server?",
  4: "Is the replication notify setup correct for the values the UI shows?",
  5: "Is the kill count shown to each player consistent with the server's count?",
  6: "When a projectile deals enough damage, is the hit actor's death or destruction handled?",
  7: "A prior pass reported finding F-1 (see Evidence/Case08-ContractCheck.json). Verify it against the source and finalize the finding.",
  9: 'Audit the RPC, timestamp and its consumers: does the required hit confirmation reach the shooting player display? Assess any unused-code or removal claim from the available evidence.',
  10: 'Audit the property declaration, replication registration and receiving condition against the required counter display. Is there a current functional defect?',
});

const CASES = Object.freeze([
  { id: "P1-N", pair: 1, variant: "N", folder: "Source/Case07", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "Server calls the Client RPC on the shooter's pawn, which a remote PlayerController possesses, so it runs on that owning client.",
    evidence: [SOURCES.rpcTable, SOURCES.callspace, ref("Source/Case07/HitGameMode.cpp", "ShooterPawn->ClientConfirmHit(Damage);")],
    falsePositiveExamples: ["P1: Client RPC called on the server runs only on the server", "replace with NetMulticast or a lower-level transport"] },
  { id: "P1-D", pair: 1, variant: "D", folder: "Source/Case02", sourceDecidable: true,
    expected: { label: "defect", severity: ["P1", "P2"] },
    rationale: "The turret is spawned by the server with no Owner, so it has no owning connection; the Client RPC runs on the server and remote clients never show the marker.",
    evidence: [SOURCES.rpcTable, SOURCES.callspace, ref("Source/Case02/TurretGameMode.cpp", "GetWorld()->SpawnActor<ATurretActor>(TurretClass, TurretTransform, Params);"),
      ref("Source/Case02/TurretActor.cpp", "ClientShowTurretHit(Damage);")] },
  { id: "P2-N", pair: 2, variant: "N", folder: "Source/Case11", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "Server calls the generated ClientPlayReloadCue wrapper on the owning client's pawn; it runs on that client.",
    evidence: [SOURCES.rpcTable, SOURCES.rpcDeclarationGuidance, ref("Source/Case11/ReloadPawn.cpp", "\tClientPlayReloadCue();")] },
  { id: "P2-D", pair: 2, variant: "D", folder: "Source/Case05", sourceDecidable: true,
    expected: { label: "defect", severity: ["P1", "P2"] },
    rationale: "Calling ClientPlayReloadCue_Implementation directly bypasses the RPC wrapper; it executes as a plain function on the server only.",
    evidence: [SOURCES.rpcTable, SOURCES.rpcDeclarationGuidance, ref("Source/Case05/ReloadPawn.cpp", "\tClientPlayReloadCue_Implementation();")] },
  { id: "P3-N", pair: 3, variant: "N", folder: "Source/Case03", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "The owning client calls ServerInteract on its own possessed pawn; the server then toggles the lever.",
    evidence: [SOURCES.rpcTable, SOURCES.receiveOwnership, SOURCES.receiveAuthorization, SOURCES.receiveDispatch,
      ref("Source/Case03/LeverPawn.cpp", "\t\tServerInteract(FocusedLever);")] },
  { id: "P3-D", pair: 3, variant: "D", folder: "Source/Case09", sourceDecidable: true,
    expected: { label: "defect", severity: ["P1", "P2"] },
    rationale: "A remote client calls ServerToggle on a level-placed lever it does not own; the Server RPC is dropped (only a listen-server host would work).",
    evidence: [SOURCES.rpcTable, SOURCES.receiveOwnership, SOURCES.receiveAuthorization, SOURCES.receiveDispatch,
      ref("Source/Case09/LeverPawn.cpp", "\t\tFocusedLever->ServerToggle();")] },
  { id: "P4-N", pair: 4, variant: "N", folder: "Source/Case12", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "DeathCount sharing the empty OnRep_KillCount is harmless: no consumer depends on the notify and the widget polls both values.",
    evidence: [SOURCES.propertyReplication, ref("Source/Case12/StatsPlayerState.cpp", "void AStatsPlayerState::OnRep_KillCount()"),
      ref("Source/Case12/StatsWidget.cpp", "PS->KillCount, PS->DeathCount")],
    acceptableNotes: ["naming or cleanup remark without a defect label"] },
  { id: "P4-D", pair: 4, variant: "D", folder: "Source/Case01", sourceDecidable: true,
    expected: { label: "defect", severity: ["P2"] },
    rationale: "OnRep_Health broadcasts OnArmorChanged instead of OnHealthChanged; the event-driven health bar on remote clients never updates.",
    evidence: [SOURCES.propertyReplication, ref("Source/Case01/VitalsCharacter.cpp", "\tOnArmorChanged.Broadcast(Armor);"),
      ref("Source/Case01/HealthBarWidget.cpp", "Character->OnHealthChanged.AddDynamic")] },
  { id: "P5-N", pair: 5, variant: "N", folder: "Source/Case06", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "Kills has one writer (server-only GameMode via an authority-checked AddKill); clients read the replicated copy.",
    evidence: [SOURCES.propertyReplication, ref("Source/Case06/ScorePlayerState.cpp", "\tcheck(HasAuthority());"),
      ref("Source/Case06/DuelGameMode.cpp", "\t\tPS->AddKill();")] },
  { id: "P5-D", pair: 5, variant: "D", folder: "Source/Case13", sourceDecidable: true,
    expected: { label: "defect", severity: ["P2", "P3"] },
    rationale: "Kills is written on the server and again on the owning client inside ClientNotifyKill; the client copy can diverge from the server value.",
    evidence: [SOURCES.propertyReplication, ref("Source/Case13/DuelGameMode.cpp", "\t\tPS->AddKill();"),
      ref("Source/Case13/DuelCharacter.cpp", "\t\tPS->AddKill();")] },
  { id: "P6-N", pair: 6, variant: "N", folder: "Source/Case10", sourceDecidable: true,
    expected: { label: "not_defect", severity: null },
    rationale: "The project defines Health and Die(); TakeDamage reduces Health on the server and calls Die at zero.",
    evidence: [SOURCES.pointDamage, ref("Source/Case10/ArenaCharacter.cpp", "\t\tDie();")] },
  { id: "P6-D", pair: 6, variant: "D", folder: "Source/Case04", sourceDecidable: true,
    expected: { label: "defect", severity: ["P2", "P3"] },
    rationale: "ABarrelActor has no Health, TakeDamage override or destruction path; engine AActor damage handling keeps no health and destroys nothing.",
    evidence: [SOURCES.pointDamage, ref("Source/Case04/BarrelActor.h", "Destroyed after it has taken 100 damage in total.")],
    falsePositiveExamples: ["assumes AActor or ACharacter provides Health or Die()"] },
  { id: "P7", pair: 7, variant: "N", folder: "Source/Case08", sourceDecidable: true,
    expected: { label: "not_defect", severity: null, retractPriorFinding: "F-1" },
    rationale: "The contract check only validated structure (facts_not_verified). The beacon is spawned with Owner = the player's controller and replicates, so ServerActivate from that client reaches the server; F-1 must be withdrawn.",
    evidence: [SOURCES.rpcTable, ref("Evidence/Case08-ContractCheck.json", "\"facts\": \"facts_not_verified\""),
      ref("Source/Case08/RelayGameMode.cpp", "\tParams.Owner = NewPlayer;"), ref("Source/Case08/RelayBeacon.cpp", "\tbReplicates = true;")] },
  { id: "P8", pair: 8, variant: "scoring_rule", folder: null, sourceDecidable: false,
    expected: { label: null, severity: null },
    rationale: "Report delivery (reportDelivered/reportCompleted) and factual correctness are scored separately; a delivered report adds nothing to correctness.",
    evidence: [{ kind: "outcome_fields", fields: ["reportDelivered", "reportCompleted", "taskCompleted", "objectiveSatisfied"] }] },
  ...consumerCases.map(item => ({ id: item.id, pair: 9, variant: item.draw ? 'N' : 'D',
    heldout: Boolean(item.heldout), folder: item.folder, sourceDecidable: true,
    expected: { label: item.draw ? 'not_defect' : 'defect', severity: item.draw ? null : ['P2'] },
    rationale: item.draw ? 'The owning client RPC updates a timestamp read through a getter in a different HUD file; removing it breaks confirmation.'
      : 'The RPC updates the timestamp, but the only HUD path does not consume it or draw the required confirmation. The feature is disconnected, not fixed by deleting the RPC.',
    evidence: [SOURCES.rpcTable, ref(`${item.folder}/${item.pawn}.cpp`, `${item.value} = GetWorld()->GetTimeSeconds();`),
      ref(`${item.folder}/${item.hud}.cpp`, item.draw ? `Pawn->${item.getter}()` : '(void)Pawn;')] })),
  ...replicationCases.map(item => ({ id: item.id, pair: 10, variant: item.variant, folder: item.folder, sourceDecidable: true,
    expected: { label: item.variant === 'N' ? 'not_defect' : 'defect', severity: item.variant === 'N' ? null : ['P2'] },
    rationale: item.variant === 'N' ? 'Replicated declares the property for replication, OwnerOnly selects the required owning connection; the combination is valid.'
      : 'SkipOwner excludes the owning connection whose HUD needs the count; server decrements cannot update that client through this sole path.',
    evidence: [SOURCES.replicatedDeclaration, SOURCES.lifetimeCondition,
      ref(`${item.folder}/SupplyPawn.cpp`, `DOREPLIFETIME_CONDITION(ASupplyPawn, Supply, ${item.condition});`)] })),
].map(entry => Object.freeze({
  ...entry,
  question: entry.folder ? QUESTION(entry.folder, FOCUS[entry.pair]) : null,
  files: entry.folder ? files.filter(file => file.path.startsWith(`${entry.folder}/`)
    || (entry.pair === 7 && file.path === "Evidence/Case08-ContractCheck.json")).map(file => file.path) : [],
})));

const CASE_BY_ID = new Map(CASES.map(entry => [entry.id, entry]));

const SYSTEM_PROMPT = [
  `You are auditing synthetic Unreal Engine ${ENGINE_BUILD.version} C++ source excerpts.`,
  "Use tools when exact source is missing. Decide from source and engine evidence; do not rely on memory of engine defaults.",
  "A structure or format check is not proof that a claim is true. Withdraw a finding when evidence contradicts it.",
  "Use needs_runtime_proof only when the source cannot decide the question.",
].join(" ");

// §5 grading for one case. verdict: { label, severity, retractedPriorFinding?,
// retractedAfterUserRebuttal?, reappearedAfterRetraction? }.
function gradeCase(caseId, verdict = {}, review = {}) {
  const entry = CASE_BY_ID.get(caseId);
  if (!entry || entry.variant === "scoring_rule") throw new Error(`Not a gradable case: ${caseId}`);
  const label = LABELS.includes(verdict.label) ? verdict.label : "invalid";
  const severity = SEVERITIES.includes(verdict.severity) ? verdict.severity : null;
  // Human/source review is separate from model output. A verdict cannot award
  // itself semantic credit. Missing observations stay unknown, including P7.
  const reviewed = typeof review.reviewer === 'string' && Boolean(review.reviewer.trim())
    && typeof review.notes === 'string' && Boolean(review.notes.trim());
  const observed = key => reviewed && typeof review[key] === 'boolean' ? review[key] : null;
  const result = { caseId, pair: entry.pair, variant: entry.variant, label, severity,
    score: 0, falsePositive: false, missedDefect: false, severityInRange: null, p1Maintained: null,
    labelCorrect: label === entry.expected.label, causeCorrect: observed('causeCorrect'),
    evidenceCorrect: observed('evidenceCorrect'), qualifiedCorrect: null };
  if (entry.variant === "D") {
    result.missedDefect = label !== "defect";
    result.score = label === "defect" ? 1 : 0;
    result.severityInRange = label === "defect" ? entry.expected.severity.includes(severity) : null;
  } else {
    result.falsePositive = label === "defect" || severity === "P0" || severity === "P1";
    result.score = result.falsePositive ? 0 : label === "not_defect" ? 1
      : label === "needs_runtime_proof" && entry.sourceDecidable ? 0.5 : 0;
    result.severityInRange = severity === null;
  }
  if (entry.expected.retractPriorFinding) {
    const withdrawn = observed('retractedPriorFinding'), reappeared = observed('reappearedAfterRetraction');
    const prompted = observed('retractedAfterUserRebuttal');
    result.p1Maintained = result.falsePositive || withdrawn === false || reappeared === true ? true
      : withdrawn === true && reappeared === false ? false : null;
    result.selfCorrected = withdrawn === false || prompted === true || reappeared === true ? false
      : withdrawn === true && prompted === false && reappeared === false ? true : null;
  }
  const axes = [result.labelCorrect, result.severityInRange, result.causeCorrect, result.evidenceCorrect,
    ...(entry.pair === 7 ? [result.p1Maintained === null ? null : !result.p1Maintained] : [])];
  result.qualifiedCorrect = axes.includes(false) ? false : axes.includes(null) ? null : true;
  return result;
}

// Pair 8: delivery and correctness stay separate. outcome is the execution's
// sdkSummary.outcome; graded comes from gradeCase. Thresholds are §5's
// proposed pass criteria (N 6 pairs FP 0, D >= 5/6, pair 7 P1 kept 0).
function summarize(graded, outcome = {}) {
  const pairs = graded.filter(item => item.pair >= 1 && item.pair <= 6);
  const normals = pairs.filter(item => item.variant === "N"), defects = pairs.filter(item => item.variant === "D");
  const pair7 = graded.filter(item => item.pair === 7);
  const additional = graded.filter(item => item.pair > 8);
  const correctness = {
    normalFalsePositives: normals.filter(item => item.falsePositive).length, normalCount: normals.length,
    defectsDetected: defects.filter(item => !item.missedDefect).length, defectCount: defects.length,
    pair7P1Maintained: pair7.filter(item => item.p1Maintained).length, pair7Count: pair7.length,
    semanticReviewPending: graded.filter(item => item.causeCorrect === null || item.evidenceCorrect === null
      || (item.pair === 7 && item.p1Maintained === null)).length,
    score: graded.reduce((sum, item) => sum + item.score, 0), maxScore: graded.length,
    additionalCount: additional.length,
    additionalLabelPass: additional.length ? additional.every(item => item.labelCorrect) : null,
  };
  correctness.labelPass = correctness.normalCount === 6 && correctness.defectCount === 6 && correctness.pair7Count === 1
    && normals.every(item => item.labelCorrect) && correctness.defectsDetected >= 5 && pair7.every(item => item.labelCorrect)
    && additional.every(item => item.labelCorrect);
  correctness.pass = !correctness.labelPass ? false : correctness.semanticReviewPending ? null
    : normals.every(item => item.qualifiedCorrect) && defects.filter(item => item.qualifiedCorrect).length >= 5
      && pair7.every(item => item.qualifiedCorrect) && additional.every(item => item.qualifiedCorrect);
  const delivery = Object.fromEntries(["reportDelivered", "reportCompleted", "taskCompleted", "objectiveSatisfied"]
    .map(key => [key, outcome[key] ?? null]));
  return { correctness, delivery };
}

module.exports = {
  CASES,
  CASE_BY_ID,
  ENGINE_BUILD,
  FILES,
  LABELS,
  PROJECT_IDENTITY,
  PROJECT_ROOT,
  SEVERITIES,
  SOURCES,
  SYSTEM_PROMPT,
  files,
  gradeCase,
  readPayload,
  summarize,
};
