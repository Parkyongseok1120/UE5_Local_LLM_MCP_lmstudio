# Unreal Asset Taxonomy For Production Work

## 검색 키워드

asset taxonomy, 에셋 종류, Material Layer, Material Function, RAG coverage, work domain, NPR

## 개요

이 문서는 **프로덕션 작업용 Unreal Engine 에셋 분류 체계**입니다. 에이전트가 프로젝트 에셋을 검색·분류·계획할 때 어떤 에셋 타입이 어떤 작업 영역(work domain)에 속하는지, 그리고 현재 RAG/MCP 파이프라인에서 어느 수준까지 인덱싱되는지를 빠르게 판단할 수 있도록 작성되었습니다.

에셋 타입명은 표에서 **영문(UE 공식 명칭)** 으로 유지하고, 설명은 한국어로 제공합니다. 상세 RAG 커버리지 매핑은 문서 말미 부록과 `config/unreal_asset_taxonomy.json`을 참조하세요.

## 작업 영역 (Work Domains)

프로덕션 작업을 10개 최상위 영역으로 분류합니다. 각 섹션(1–21)은 아래 영역 중 하나에 매핑됩니다.

1. **렌더링** (`rendering`) — Rendering
2. **월드·레벨** (`world_level`) — World & Level
3. **캐릭터·애니메이션** (`character_animation`) — Character & Animation
4. **게임플레이·로직** (`gameplay_logic`) — Gameplay & Logic
5. **UI** (`ui`) — UI
6. **사운드** (`sound`) — Sound
7. **VFX** (`vfx`) — VFX
8. **AI** (`ai`) — AI
9. **데이터·밸런스** (`data_balance`) — Data & Balance
10. **에디터·빌드** (`editor_build`) — Editor & Build

## NPR / 카툰·스타일라이즈드 렌더링 참고

NPR(Non-Photorealistic Rendering) 및 카툰 스타일 작업에서는 다음 에셋 타입이 특히 중요합니다.

- **Material** — 마스터 셰이더·셀 셰이딩·라인 아트 베이스
- **Material Instance (MI)** — 캐릭터/환경별 파라미터 변형
- **Material Function** — 재사용 노이즈·림라이트·스텐실 유틸
- **Post Process Material** — 아웃라인·컬러 그레이딩·스타일라이즈 후처리
- **Material Parameter Collection (MPC)** — 전역 라이트/윤곽/시간 파라미터

표에서 `NPR` 열이 `✓`인 항목은 NPR 관련 작업 시 우선 검색·검증 대상입니다.

## 1. 메시·지오메트리

**작업 영역:** 렌더링 (`rendering`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Static Mesh | 정적 3D 메시. 콜리전·LOD·나나이트 설정을 포함하는 비스켈레탈 지오메트리 자산. | /Game/Environment/SM_Wall, /Game/Props/SM_Crate | StaticMesh | registry |  |
| Skeletal Mesh | 스켈레톤에 바인딩된 변형 가능 메시. 캐릭터·무기 등 애니메이션 대상. | /Game/Characters/SK_Hero, /Game/Weapons/SK_Rifle | SkeletalMesh | graph_animation |  |
| Geometry Collection | Chaos 파괴 시뮬레이션용 프랙처 지오메트리 컬렉션. | /Game/Destruction/GC_Wall | GeometryCollection | registry |  |
| Nanite Mesh | 나나이트가 활성화된 Static Mesh. 고밀도 지오메트리 가상화 렌더링. | /Game/Environment/SM_Rock_Nanite | StaticMesh | registry |  |
| Groom Asset | 머리카락·털·수염 등 그룸(Hair) 시뮬레이션/렌더링 자산. | /Game/Characters/Groom_Hair | GroomAsset, Groom | registry |  |
| Spline Mesh | 스플라인을 따라 배치·변형되는 메시 컴포넌트용 메시(도로·파이프 등). | /Game/Environment/SM_Road_Spline | StaticMesh | registry |  |
| Landscape | 대규모 지형. 높이맵·레이어·스컬프트 페인트 기반 월드 지형. | /Game/Maps/Landscape_Main | Landscape | registry |  |
| Landscape Layer | 랜드스케이프 페인트 레이어 정의. 머티리얼 블렌드·텍스처 가중치. | Layer_Grass, Layer_Rock | LandscapeLayerInfoObject | registry |  |
| Foliage Type | 폴리지 인스턴싱 타입. 잔디·나무 등 반복 배치 규칙. | /Game/Foliage/FT_Grass | FoliageType_InstancedStaticMesh | registry |  |
| Packed Level Actor | 레벨 인스턴스/패킹된 액터 블루프린트. 반복 월드 청크 배치. | /Game/World/Packed/BP_BuildingCluster | PackedLevelActor, PackedLevelActorBlueprint | registry |  |

## 2. 머티리얼·셰이더

**작업 영역:** 렌더링 (`rendering`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Material | 마스터 머티리얼. 셰이더 그래프·머티리얼 도메인·출력 정의. | /Game/Materials/M_Master, /Game/Characters/M_Character | Material | graph_material | ✓ |
| Material Instance | 마스터 머티리얼 인스턴스. 파라미터 오버라이드만 적용하는 MI. | /Game/Materials/MI_Wood, /Game/Characters/MI_Skin | MaterialInstanceConstant, MaterialInstance | graph_material | ✓ |
| Material Function | 재사용 가능한 머티리얼 함수 노드 그래프. | /Game/Materials/Functions/MF_UVNoise | MaterialFunction | registry | ✓ |
| Material Layer | 레이어드 머티리얼용 단일 레이어 정의. | /Game/Materials/Layers/ML_Base | MaterialFunctionMaterialLayer | registry |  |
| Material Layer Blend | 머티리얼 레이어 블렌드 함수. | /Game/Materials/Layers/MLB_Blend | MaterialFunctionMaterialLayerBlend | registry |  |
| Material Parameter Collection | 전역 스칼라/벡터 머티리얼 파라미터 컬렉션(MPC). | /Game/Materials/MPC_Global | MaterialParameterCollection | registry | ✓ |
| Substrate Material | Substrate 기반 머티리얼/슬랩. UE5 고급 머티리얼 시스템. | /Game/Materials/M_Substrate | Material | not_exported_yet |  |
| Decal Material | 데칼 도메인 머티리얼. 벽면 스티커·손상·투사 텍스처. | /Game/Decals/M_Decal_BulletHole | Material, MaterialInstanceConstant | graph_material |  |
| Post Process Material | 포스트 프로세스용 머티리얼. 블룸·컬러 그레이딩·스타일라이즈 효과. | /Game/PostProcess/PP_Outline, /Game/PostProcess/PP_CelShade | Material, MaterialInstanceConstant | graph_material | ✓ |

## 3. 텍스처

**작업 영역:** 렌더링 (`rendering`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Texture 2D | 기본 2D 텍스처. 알베도·디퓨즈 등 범용 이미지. | /Game/Textures/T_Albedo, /Game/Textures/T_Diffuse | Texture2D | registry | ✓ |
| Normal Map | 노멀 맵 텍스처. 표면 디테일 법선 정보. | /Game/Textures/T_Normal | Texture2D | registry | ✓ |
| ORM Texture | Occlusion·Roughness·Metallic 패킹 텍스처. | /Game/Textures/T_ORM | Texture2D | registry |  |
| Mask Texture | 마스크/채널 분리용 텍스처. R/G/B/A 각 용도 분리. | /Game/Textures/T_Mask | Texture2D | registry | ✓ |
| Emissive Texture | 이미시브(발광) 텍스처. 네온·스크린 발광. | /Game/Textures/T_Emissive | Texture2D | registry | ✓ |
| Texture Cube | 큐브맵 텍스처. 스카이·반사·IBL. | /Game/Textures/T_SkyCubemap | TextureCube | registry |  |
| Render Target | 런타임/에디터 렌더 타겟. 오프스크린 렌더 결과. | /Game/RenderTargets/RT_SceneCapture | TextureRenderTarget2D, TextureRenderTarget | registry |  |
| Virtual Texture | 런타임 가상 텍스처(RVT 소스 또는 VT 볼륨). | /Game/VT/VT_Landscape | RuntimeVirtualTexture, VirtualTextureBuilder | registry |  |
| Runtime Virtual Texture | 런타임 가상 텍스처 볼륨·머티리얼 출력. | /Game/VT/RVT_Ground | RuntimeVirtualTexture | registry |  |
| Texture Light Profile | IES 라이트 프로파일 텍스처. | /Game/Lighting/T_IES_Profile | TextureLightProfile | registry |  |
| Media Texture | 미디어 플레이어 출력 텍스처. | /Game/Media/MT_VideoScreen | MediaTexture | registry |  |

## 4. 라이팅·렌더링

**작업 영역:** 렌더링 (`rendering`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Light Function Material | 라이트 함수 머티리얼. 조명 패턴·깜빡임·고스트 효과. | /Game/Lighting/M_LightFunction | Material, MaterialInstanceConstant | graph_material |  |
| IES Profile | IES 조명 분포 프로파일. | /Game/Lighting/IES_Spot | TextureLightProfile | registry |  |
| Sky Atmosphere | 대기 산란 기반 스카이·태양·안개 색. | SkyAtmosphere in level | SkyAtmosphere | registry |  |
| Volumetric Cloud | 볼류메트릭 클라우드 액터/설정. | VolumetricCloud in level | VolumetricCloud | registry |  |
| Exponential Height Fog | 지수 높이 안개. 거리·높이 기반 포그. | ExponentialHeightFog in level | ExponentialHeightFog | registry |  |
| Post Process Volume | 포스트 프로세스 볼륨. 노출·블룸·모션 블러·LUT 적용. | /Game/Maps/PPV_Main | PostProcessVolume | registry |  |
| Color Grading LUT | 컬러 그레이딩 LUT 텍스처. | /Game/PostProcess/T_LUT_Cinematic | Texture2D | registry |  |
| Reflection Capture | 스피어/박스 리플렉션 캡처. 로컬 반사. | SphereReflectionCapture in level | SphereReflectionCapture, BoxReflectionCapture | registry |  |
| Lightmass Importance Volume | 라이트매스 베이크 중요도 볼륨. | LightmassImportanceVolume in level | LightmassImportanceVolume | registry |  |
| Lighting Build Data | 라이트매스/라이팅 빌드 데이터 맵 에셋. | /Game/Maps/LightingBuildData | MapBuildDataRegistry | registry |  |

## 5. 애니메이션

**작업 영역:** 캐릭터·애니메이션 (`character_animation`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Skeleton | 본 계층·소켓 정의. 스켈레탈 메시·애니 공통 골격. | /Game/Characters/SKEL_Hero | Skeleton | registry |  |
| Skeletal Mesh | 애니메이션 섹션의 스켈레탈 메시. 본·모프·LOD 메타 포함. | /Game/Characters/SK_Hero | SkeletalMesh | graph_animation |  |
| Animation Sequence | 키프레임 애니메이션 시퀀스. | /Game/Animations/AS_Idle, /Game/Animations/AS_Run | AnimSequence | graph_animation |  |
| Animation Montage | 몽타주. 섹션·노티파이·블렌드가 있는 복합 재생 단위. | /Game/Animations/AM_Attack | AnimMontage | graph_animation |  |
| Blend Space | 1D/2D 블렌드 스페이스. 속도·방향 등에 따른 애니 블렌드. | /Game/Animations/BS_Locomotion | BlendSpace, BlendSpace1D | registry |  |
| Aim Offset | 에임 오프셋 블렌드 스페이스. 상하좌우 조준. | /Game/Animations/AO_Rifle | AimOffsetBlendSpace, BlendSpace | registry |  |
| Anim Blueprint | 애니메이션 블루프린트. 애니 그래프·상태 머신. | /Game/Characters/ABP_Hero | AnimBlueprint | graph_animation |  |
| Linked Anim Graph | 링크드 애니 그래프. 모듈형 애니BP 서브그래프. | /Game/Characters/Linked_ABP_UpperBody | AnimBlueprint | graph_animation |  |
| Control Rig | 컨트롤 릭. 프로시저럴 리깅·IK. | /Game/Characters/CR_Hero | ControlRigBlueprint | not_exported_yet |  |
| IK Rig | IK 리그 정의. 풀바디 IK 체인. | /Game/Characters/IKR_Hero | IKRigDefinition | not_exported_yet |  |
| IK Retargeter | 스켈레톤 간 IK 리타겟 자산. | /Game/Characters/IKRT_HeroToMannequin | IKRetargeter | not_exported_yet |  |
| Pose Asset | 포즈 에셋. 정적/커브 포즈 스냅샷. | /Game/Animations/PA_HandPoses | PoseAsset | registry |  |
| Physics Asset | 물리 애셋. 라그돌·콜리전 바디·제약. | /Game/Characters/PHYS_Hero | PhysicsAsset | registry |  |
| Cloth Asset | Chaos/클로스 시뮬레이션 설정 자산. | /Game/Characters/Cloth_Cape | ChaosClothConfig, ClothingSimulationFactory | not_exported_yet |  |

## 6. 블루프린트·로직

**작업 영역:** 게임플레이·로직 (`gameplay_logic`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Blueprint Class | 일반 블루프린트 클래스. AActor/UObject 파생. | /Game/Blueprints/BP_Item | Blueprint | graph_blueprint |  |
| Actor Blueprint | 액터 블루프린트. 월드에 배치 가능한 로직. | /Game/Blueprints/BP_Door | Blueprint | graph_blueprint |  |
| Pawn Blueprint | 폰 블루프린트. 플레이어/AI 조종 대상. | /Game/Blueprints/BP_Vehicle | Blueprint | graph_blueprint |  |
| Character Blueprint | 캐릭터 블루프린트. 캐릭터 무브먼트·메시 포함. | /Game/Characters/BP_Hero | Blueprint | graph_blueprint |  |
| Player Controller | 플레이어 컨트롤러 BP. 입력·HUD·카메라. | /Game/Blueprints/BP_PlayerController | Blueprint | graph_blueprint |  |
| GameMode | 게임 모드 BP. 규칙·스폰·매치 흐름. | /Game/Blueprints/BP_GameMode | Blueprint | graph_blueprint |  |
| GameState | 게임 스테이트 BP. 매치 전역 상태. | /Game/Blueprints/BP_GameState | Blueprint | graph_blueprint |  |
| PlayerState | 플레이어 스테이트 BP. 점수·팀·개인 상태. | /Game/Blueprints/BP_PlayerState | Blueprint | graph_blueprint |  |
| Actor Component | 액터 컴포넌트 BP. 재사용 로직 모듈. | /Game/Components/BP_HealthComponent | Blueprint | graph_blueprint |  |
| Scene Component | 씬 컴포넌트 BP. 트랜스폼 계층용. | /Game/Components/BP_SceneRoot | Blueprint | graph_blueprint |  |
| Blueprint Interface | 블루프린트 인터페이스. 다형 계약. | /Game/Interfaces/BPI_Interactable | Blueprint | graph_blueprint |  |
| Blueprint Function Library | 블루프린트 함수 라이브러리. 정적 유틸 함수. | /Game/Blueprints/BFL_Gameplay | Blueprint | graph_blueprint |  |
| Macro Library | 매크로 라이브러리. 재사용 매크로 노드. | /Game/Blueprints/BPML_Common | Blueprint | graph_blueprint |  |
| Editor Utility Blueprint | 에디터 유틸리티 블루프린트. 에디터 자동화. | /Game/Editor/EUBP_AssetTools | Blueprint | graph_blueprint |  |
| Editor Utility Widget | 에디터 유틸리티 위젯. 에디터 UI 패널. | /Game/Editor/EUW_Pipeline | WidgetBlueprint, EditorUtilityWidgetBlueprint | graph_blueprint |  |

## 7. 레벨·월드

**작업 영역:** 월드·레벨 (`world_level`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Level | 맵/레벨 .umap 자산. | /Game/Maps/L_Main, /Game/Maps/L_Menu | World, Level | registry |  |
| World | 월드 에셋. 레벨 컬렉션 루트. | /Game/Maps/L_Main | World | registry |  |
| Persistent Level | 월드의 퍼시스턴트 레벨. 항상 로드되는 기본 레벨. | Persistent level in L_Main | World, Level | registry |  |
| Sub Level | 서브 레벨. 스트리밍/레벨 스트리밍 단위. | /Game/Maps/Sub/L_City_Block | World, Level | registry |  |
| Level Instance | 레벨 인스턴스. 재사용 맵 청크의 인스턴스 배치. | /Game/World/LevelInstances/LI_Building | World, LevelInstance | registry |  |
| Packed Level Blueprint | 패킹된 레벨 블루프린트. 대량 인스턴싱용. | /Game/World/BP_PackedDistrict | PackedLevelActorBlueprint, Blueprint | registry |  |
| World Partition | 월드 파티션. 대형 오픈 월드 스트리밍 그리드. | World Partition in L_OpenWorld | WorldPartition | registry |  |
| Data Layer | 데이터 레이어. 런타임/에디터 레이어 가시성·로드. | DL_Gameplay, DL_Cinematic | DataLayerAsset | registry |  |
| HLOD | 계층적 LOD. 원거리 합성 메시/액터. | /Game/Maps/HLOD/HLOD0_Streaming | HLODLayer, WorldPartitionHLOD | registry |  |
| Map Build Data Registry | 맵 빌드 데이터 레지스트리. 라이팅·나비 빌드 결과. | /Game/Maps/L_Main_BuiltData | MapBuildDataRegistry | registry |  |

## 8. 데이터·밸런스

**작업 영역:** 데이터·밸런스 (`data_balance`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Data Asset | 일반 데이터 에셋. 구조화된 디자인 데이터. | /Game/Data/DA_WeaponConfig | DataAsset | registry |  |
| Primary Data Asset | 프라이머리 데이터 에셋. 에셋 매니저 ID 기반. | /Game/Data/PDA_Item | PrimaryDataAsset | registry |  |
| Data Table | 데이터 테이블. 행 기반 CSV형 밸런스. | /Game/Data/DT_Weapons | DataTable | registry |  |
| Curve Float | 스칼라 커브. 시간/레벨 대비 float. | /Game/Data/Curve_DamageFalloff | CurveFloat | registry |  |
| Curve Vector | 벡터 커브. 3축 값 곡선. | /Game/Data/Curve_Movement | CurveVector | registry |  |
| Curve Linear Color | 선형 컬러 커브. 색/그라데이션 키. | /Game/Data/Curve_SkyTint | CurveLinearColor | registry |  |
| Curve Table | 커브 테이블. 행별 커브 참조. | /Game/Data/CT_LevelScaling | CurveTable | registry |  |
| String Table | 문자열 테이블. 로컬라이제이션 키·텍스트. | /Game/Localization/ST_UI | StringTable | registry |  |
| Gameplay Tags | 게임플레이 태그. 계층적 상태/분류 태그. | Ability.Attack.Melee, State.Dead | GameplayTagsManager | registry |  |
| Gameplay Tag Table | 게임플레이 태그 테이블/INI 기반 태그 정의. | DefaultGameplayTags.ini | DataTable | registry |  |
| Data Registry | 데이터 레지스트리. 런타임 조회 가능 데이터 소스. | /Game/Data/DR_Items | DataRegistry | registry |  |

## 9. GAS (Gameplay Ability System)

**작업 영역:** 게임플레이·로직 (`gameplay_logic`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Gameplay Ability | 게임플레이 어빌리티. 스킬·액션 로직. | /Game/Abilities/GA_Fireball | Blueprint, GameplayAbility | graph_blueprint |  |
| Gameplay Effect | 게임플레이 이펙트. 버프/디버프·속성 수정. | /Game/Abilities/GE_Burn | Blueprint, GameplayEffect | graph_blueprint |  |
| Attribute Set | 속성 세트. Health·Mana 등 GAS 속성. | /Game/Abilities/AS_HeroAttributes | Blueprint | graph_blueprint |  |
| Gameplay Cue | 게임플레이 큐. VFX/SFX/카메라 등 이펙트 트리거. | /Game/Abilities/GC_HitImpact | Blueprint, GameplayCueNotify_Static | graph_blueprint |  |
| Gameplay Tag | GAS 컨텍스트용 게임플레이 태그 참조. | Ability.Cooldown.Fireball | GameplayTagsManager | registry |  |
| Ability Task | 어빌리티 태스크. 비동기 어빌리티 노드(대기·타겟팅 등). | AbilityTask_WaitDelay, AbilityTask_PlayMontageAndWait | Blueprint | graph_blueprint |  |

## 10. AI

**작업 영역:** AI (`ai`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| AI Controller | AI 컨트롤러 BP. BT 실행·퍼셉션. | /Game/AI/BP_AIController | Blueprint | graph_blueprint |  |
| Behavior Tree | 비헤이비어 트리. AI 의사결정 트리. | /Game/AI/BT_Enemy | BehaviorTree | registry |  |
| Blackboard | 블랙보드. BT 키·메모리. | /Game/AI/BB_Enemy | BlackboardData | registry |  |
| EQS Query | 환경 쿼리 시스템. 위치/타겟 스코어링. | /Game/AI/EQS_FindCover | EnvQuery | registry |  |
| NavMesh | 내비게이션 메시 빌드 데이터. | NavMesh in L_Main | NavigationData, RecastNavMesh | registry |  |
| Nav Modifier Volume | 내비 수정 볼륨. 영역별 이동 비용·차단. | NavModifierVolume in level | NavModifierVolume | registry |  |
| Smart Object | 스마트 오브젝트 정의. 상호작용 슬롯 AI. | /Game/AI/SO_Bench | SmartObjectDefinition | registry |  |
| Perception Component | AI 퍼셉션 컴포넌트. 시야·청각 감지. | AIPerceptionComponent on BP_AIController | Blueprint | graph_blueprint |  |

## 11. 사운드

**작업 영역:** 사운드 (`sound`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Sound Wave | 원시 사운드 웨이브. WAV 등 오디오 소스. | /Game/Audio/SW_Footstep | SoundWave | registry |  |
| Sound Cue | 사운드 큐. 노드 그래프 기반 사운드 믹스. | /Game/Audio/SC_Gunshot | SoundCue | registry |  |
| MetaSound Source | MetaSound 소스. 프로시저럴 오디오 그래프. | /Game/Audio/MS_Engine | MetaSoundSource | registry |  |
| MetaSound Patch | MetaSound 패치. 재사용 오디오 서브그래프. | /Game/Audio/MP_Filter | MetaSoundPatch | registry |  |
| Sound Attenuation | 감쇠 설정. 거리·공간화. | /Game/Audio/ATT_Default | SoundAttenuation | registry |  |
| Sound Concurrency | 동시 재생 제한 규칙. | /Game/Audio/CONC_Gunshots | SoundConcurrency | registry |  |
| Sound Class | 사운드 클래스. 믹스 계층·볼륨 그룹. | /Game/Audio/SC_Master | SoundClass | registry |  |
| Sound Mix | 사운드 믹스. 클래스별 EQ/볼륨 오버라이드. | /Game/Audio/SM_Combat | SoundMix | registry |  |
| Reverb Effect | 리버브 이펙트 프리셋. | /Game/Audio/REVERB_Cave | ReverbEffect | registry |  |
| Dialogue Wave | 대화 웨이브. 보이스 라인·자막 연동. | /Game/Audio/DLG_Intro | DialogueWave | registry |  |
| Media Sound Component | 미디어 사운드 컴포넌트. 비디오 오디오 출력. | MediaSoundComponent on BP_VideoScreen | Blueprint | graph_blueprint |  |

## 12. VFX

**작업 영역:** VFX (`vfx`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Niagara System | 나이아가라 시스템. 파티클/VFX 메인 에셋. | /Game/VFX/NS_Explosion, /Game/VFX/NS_MagicTrail | NiagaraSystem | registry | ✓ |
| Niagara Emitter | 나이아가라 이미터. 서브 이펙트 모듈. | /Game/VFX/NE_Sparks | NiagaraEmitter | registry |  |
| Niagara Module Script | 나이아가라 모듈 스크립트. 커스텀 HLSL/노드. | /Game/VFX/NMS_CustomForce | NiagaraScript | registry |  |
| Niagara Parameter Collection | 나이아가라 파라미터 컬렉션. 전역 VFX 파라미터. | /Game/VFX/NPC_Global | NiagaraParameterCollection | registry |  |
| Niagara Data Interface | 나이아가라 데이터 인터페이스. 메시/스플라인 등 샘플링. | /Game/VFX/NDI_SkeletalMesh | NiagaraDataInterface | not_exported_yet |  |
| Cascade Particle System | 레거시 캐스케이드 파티클. | /Game/VFX/PS_LegacySmoke | ParticleSystem | registry |  |
| Material-based VFX | 머티리얼 기반 VFX. 메시/데칼/포스트 머티리얼 이펙트. | /Game/VFX/M_Dissolve, /Game/VFX/MI_Shield | Material, MaterialInstanceConstant | graph_material |  |

## 13. UI

**작업 영역:** UI (`ui`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Widget Blueprint | 위젯 블루프린트. UMG UI 레이아웃·로직. | /Game/UI/WBP_HUD, /Game/UI/WBP_Inventory | WidgetBlueprint | graph_blueprint | ✓ |
| User Widget | 유저 위젯 클래스. WBP가 생성하는 런타임 위젯. | UUserWidget subclass from WBP_HUD | WidgetBlueprint | graph_blueprint |  |
| Font | 폰트 에셋. UMG/Slate 텍스트. | /Game/UI/Font_Noto | Font, FontFace | registry |  |
| Slate Brush | 슬레이트 브러시. UI 이미지·타일·테두리. | /Game/UI/Brush_Button | SlateBrushAsset | registry |  |
| Texture Atlas | UI 텍스처 아틀라스. 스프라이트 시트. | /Game/UI/Atlas_Icons | Texture2D | registry |  |
| Common UI Asset | Common UI 스타일/입력 데이터. | /Game/UI/CommonUI/Style_Default | CommonUIVisibilitySubsystem | not_exported_yet |  |
| Input Action Icon Data | 입력 액션 아이콘 매핑 데이터. | /Game/UI/InputIcons/IA_Icons | DataAsset | registry |  |
| UI Material | UI 머티리얼 도메인. UMG 머티리얼 브러시용. | /Game/UI/M_UI_Gradient | Material, MaterialInstanceConstant | graph_material |  |
| Widget Animation | 위젯 애니메이션. UMG 타임라인. | FadeIn animation in WBP_HUD | WidgetBlueprint | graph_blueprint |  |

## 14. 입력

**작업 영역:** 게임플레이·로직 (`gameplay_logic`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Input Action | Enhanced Input 액션. | /Game/Input/IA_Jump, /Game/Input/IA_Fire | InputAction | registry |  |
| Input Mapping Context | 입력 매핑 컨텍스트. 액션·키 바인딩 묶음. | /Game/Input/IMC_Default | InputMappingContext | registry |  |
| Input Modifier | 입력 수정자. 스케일·데드존·스와즐. | InputModifierDeadZone | InputModifier | registry |  |
| Input Trigger | 입력 트리거. Pressed/Released/Hold 등. | InputTriggerPressed | InputTrigger | registry |  |
| Force Feedback Effect | 패드 포스 피드백 이펙트. | /Game/Input/FFE_Rumble | ForceFeedbackEffect | registry |  |
| Haptic Feedback Effect | 햅틱 피드백 이펙트(모바일/패드). | /Game/Input/HFE_Tap | HapticFeedbackEffect | registry |  |

## 15. 물리

**작업 영역:** 게임플레이·로직 (`gameplay_logic`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Physics Asset | 물리 섹션의 Physics Asset. 충돌·라그돌. | /Game/Characters/PHYS_Hero | PhysicsAsset | registry |  |
| Physical Material | 피지컬 머티리얼. 마찰·반발·사운드 서피스. | /Game/Physics/PM_Concrete | PhysicalMaterial | registry |  |
| Collision Preset | 콜리전 프리셋. 프로젝트/에디터 설정. | BlockAll, Pawn | — | guidelines |  |
| Destructible/Geometry Collection | 파괴 가능 오브젝트. Geometry Collection 기반. | /Game/Destruction/GC_Wall | GeometryCollection | registry |  |
| Chaos Cloth Config | Chaos 클로스 설정. | /Game/Physics/CC_Cloth | ChaosClothConfig | not_exported_yet |  |
| Constraint Setup | 물리 제약 설정. 힌지·볼 등. | PhysicsConstraint in BP_Door | Blueprint | graph_blueprint |  |
| Cable Component | 케이블 컴포넌트. 물리 시뮬 로프. | CableComponent on BP_Crane | Blueprint | graph_blueprint |  |

## 16. 시네마틱

**작업 영역:** 캐릭터·애니메이션 (`character_animation`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Level Sequence | 레벨 시퀀스. 시퀀서 메인 타임라인. | /Game/Cinematics/LS_Intro | LevelSequence | graph_animation |  |
| Sub Sequence | 서브 시퀀스. 중첩 시퀀서 샷. | /Game/Cinematics/Sub/LS_Shot01 | LevelSequence | graph_animation |  |
| Camera Animation Sequence | 카메라 애니메이션 시퀀스. | /Game/Cinematics/Camera/CA_Shake | CameraAnim | registry |  |
| Cine Camera Actor | 시네 카메라 액터. 렌즈·필름백 설정. | CineCameraActor in LS_Intro | Blueprint | graph_blueprint |  |
| Movie Render Queue Preset | 무비 렌더 큐 프리셋. 고품질 시퀀스 렌더. | /Game/Cinematics/MRQ_4K | MoviePipelineQueue | not_exported_yet |  |
| Take Recorder Data | 테이크 리코더 데이터. 모션캡처/라이브 테이크. | /Game/Cinematics/Takes/Take_001 | TakeRecorderSources | not_exported_yet |  |
| Control Rig Sequence | 컨트롤 릭 시퀀스. 시퀀서 내 릭 애니. | /Game/Cinematics/CR_Seq_Hero | ControlRigSequence | not_exported_yet |  |

## 17. 미디어

**작업 영역:** 사운드 (`sound`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Media Player | 미디어 플레이어. 비디오 재생 제어. | /Game/Media/MP_Main | MediaPlayer | registry |  |
| Media Source | 미디어 소스 추상 베이스. | /Game/Media/MS_Trailer | MediaSource | registry |  |
| File Media Source | 파일 미디어 소스. 로컬 비디오 파일. | /Game/Media/File/MS_VideoFile | FileMediaSource | registry |  |
| Img Media Source | 이미지 시퀀스 미디어 소스. | /Game/Media/Img/MS_ImageSequence | ImgMediaSource | registry |  |
| Media Texture | 미디어 텍스처. 비디오 프레임 출력. | /Game/Media/MT_Screen | MediaTexture | registry |  |
| Media Sound Component | 미디어 사운드. 비디오 오디오 트랙. | MediaSoundComponent on BP_VideoScreen | Blueprint | graph_blueprint |  |

## 18. 에디터 자동화

**작업 영역:** 에디터·빌드 (`editor_build`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Editor Utility Widget | 에디터 유틸리티 위젯. 커스텀 에디터 패널. | /Game/Editor/EUW_ExportTools | EditorUtilityWidgetBlueprint, WidgetBlueprint | graph_blueprint |  |
| Editor Utility Blueprint | 에디터 유틸리티 BP. 에디터 스크립트 액션. | /Game/Editor/EUBP_BatchRename | Blueprint | graph_blueprint |  |
| Blutility | 블루프린트 유틸리티(Blutility) 레거시 명칭. | /Game/Editor/Blutility_Tools | Blueprint | graph_blueprint |  |
| Python Script | UE Editor Python 스크립트. tools/ue_export 등. | tools/ue_export/export_material_metadata.py | — | source_code |  |
| Data Validation Rule | 데이터 검증 규칙. 에셋 품질 검사. | /Game/Editor/DVR_CheckTextures | EditorValidatorBase | not_exported_yet |  |
| Asset Action Utility | 에셋 액션 유틸리티. 콘텐츠 브라우저 우클릭 메뉴. | /Game/Editor/AAU_ExportMetadata | AssetActionUtility | not_exported_yet |  |
| Commandlet | 커맨들릿. 헤드리스 에디터 배치 작업. | UAssetRegistryExportCommandlet | — | source_code |  |

## 19. 에셋 레지스트리 메타데이터

**작업 영역:** 에디터·빌드 (`editor_build`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| AssetName | 에셋 짧은 이름(패키지 내 오브젝트명). | M_Hero, BP_Player | — | registry |  |
| PackageName | 패키지 전체 이름(/Game/... 경로). | /Game/Materials/M_Hero | — | registry |  |
| PackagePath | 패키지 디렉터리 경로. | /Game/Materials | — | registry |  |
| ObjectPath | 오브젝트 전체 경로(패키지.오브젝트). | /Game/Materials/M_Hero.M_Hero | — | registry |  |
| AssetClassPath | 에셋 클래스 경로(모듈·클래스명). | /Script/Engine.Material, /Script/Engine.Blueprint | — | registry |  |
| TagsAndValues | 에셋 레지스트리 태그·값 딕셔너리. | {"SkeletalMesh": "SK_Hero"} | — | registry |  |
| ChunkIDs | 패키징 청크 ID 목록. | [0], [1, 2] | — | registry |  |
| PackageFlags | 패키지 플래그 비트마스크. | PKG_FilterEditorOnly | — | registry |  |
| SoftObjectPath | 소프트 오브젝트 경로 문자열. | /Game/Materials/M_Hero.M_Hero | — | registry |  |

## 20. 참조

**작업 영역:** 에디터·빌드 (`editor_build`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Hard Reference | 하드 참조. 로드 시 의존 에셋 강제 로드. | Static mesh reference on BP_Prop | — | registry |  |
| Soft Reference | 소프트 참조. TSoftObjectPtr 등 지연 로드. | TSoftObjectPtr<UTexture2D> | — | registry |  |
| Soft Object Path | FSoftObjectPath 문자열 경로. | /Game/Textures/T_Icon.T_Icon | — | registry |  |
| Soft Class Reference | TSoftClassPtr 클래스 소프트 참조. | TSoftClassPtr<AActor> | — | registry |  |
| Primary Asset ID | FPrimaryAssetId. 에셋 매니저 식별자. | Item:Weapon_Sword | — | registry |  |
| Asset Bundle | 에셋 번들. 청크/스테이징 묶음. | Game, Menu, Maps | — | registry |  |

## 21. 패키징

**작업 영역:** 에디터·빌드 (`editor_build`)

| Asset Type (EN) | 설명 | 예시 | UE Asset Class | RAG | NPR |
|---|---|---|---|---|---|
| Cooked Asset | 쿡된 플랫폼별 에셋. Staging/Saved/Cooked. | Saved/Cooked/Windows/... | — | path_only |  |
| Derived Data Cache | DDC. 셰이더·메시·텍스처 파생 캐시. | DerivedDataCache/... | — | not_exported_yet |  |
| Shader Cache | 셰이더 파이프라인 캐시. | PipelineCaches/... | — | not_exported_yet |  |
| Asset Registry.bin | 쿡된 에셋 레지스트리 바이너리. | Metadata/DevelopmentAssetRegistry.bin | — | registry |  |
| Pak/IoStore | Pak 또는 IoStore 컨테이너. | Content/Paks/game.pak, *.utoc/*.ucas | — | path_only |  |
| Chunk Data | 패키징 청크별 에셋 묶음. | Chunk 0: core, Chunk 1: maps | — | registry |  |
| Redirector | 오브젝트 리다이렉터. 이동/리네임 추적. | /Game/Old/M_Redirector | ObjectRedirector | registry |  |

## Appendix: RAG / MCP 인덱스 커버리지 (이 리포)

이 리포지토리의 RAG 인덱스는 에셋 타입별로 **세 가지 주요 티어**로 나뉩니다. 에이전트는 그래프 수준의 주장(노드·핀·와이어)을 하기 전에 해당 에셋이 어느 티어까지 커버되는지 반드시 확인해야 합니다.

### 커버리지 티어 요약

1. **path_only** — `unreal_project_asset_path` 소스만. `.uasset` 경로·파일명 수준의 최소 커버리지.
2. **registry** — `unreal_asset_registry` 소스. `AssetClassPath`, `PackagePath`, `ObjectPath` 등 레지스트리 메타데이터(자산 타입 + 경로).
3. **graph export** — 에디터 익스포트 기반 그래프/구조 메타데이터. 머티리얼·블루프린트·애니메이션 등 타입별 전용 소스.

### RAG 소스 · MCP 도구 · 현재 상태

| rag_coverage level | RAG source | MCP tool | 현재 상태 |
|---|---|---|---|
| path_only | unreal_project_asset_path | unreal_rag_search | 경로·파일명만 인덱싱. 그래프/레지스트리 필드 없음. |
| registry | unreal_asset_registry | unreal_rag_search (mode=material_analysis 등) | MaterialFunction, MaterialLayer, MPC, PhysicalMaterial, Texture2D, BehaviorTree 등 대부분 비그래프 에셋. 타입·경로·태그 수준. |
| graph_material | unreal_material_metadata | unreal_asset_graph_lookup, unreal_material_claim_validate | Material·MI·MaterialFunction·MaterialLayer·LayerBlend 그래프/파라미터 익스포트. MPC는 파라미터 메타만. |
| graph_blueprint | unreal_blueprint_metadata | unreal_asset_graph_lookup, unreal_blueprint_claim_validate | Blueprint 클래스·변수·함수·그래프/노드/핀 요약 익스포트. |
| graph_animation | unreal_animation_metadata (+ LevelSequence 등) | unreal_rag_search, unreal_asset_graph_lookup (제한적) | AnimSequence, AnimMontage, AnimBlueprint, SkeletalMesh, LevelSequence 등. |
| source_code | unreal_source | unreal_rag_search, unreal_symbol_lookup | 프로젝트 C++/H, .usf/.ush 셰이더 텍스트. |
| guidelines | project_guideline | unreal_rag_search | RAG_Project_Guidelines 문서 전용. |
| not_exported_yet | — | unreal_run_editor_export (확장 필요) | Substrate 슬랩, Control Rig, IK Rig, Niagara DI, MRQ 프리셋 등 파이프라인 공백. |

### 기계 판독용 매핑

에셋 클래스 → 작업 영역·RAG 커버리지·NPR 플래그 매핑은 [`config/unreal_asset_taxonomy.json`](../../config/unreal_asset_taxonomy.json)에 정의되어 있습니다. `scripts/asset_taxonomy.py`가 RAG 검색 결과와 MCP `unreal_asset_graph_lookup` 응답에 taxonomy 힌트를 붙입니다.

### 예시: Material Layer (`ML_BaseColor`)

| 항목 | 값 |
|---|---|
| UE Asset Class | `MaterialFunctionMaterialLayer` |
| Taxonomy 항목 | Material Layer |
| rag_coverage | `graph_material` |
| `unreal_asset_graph_lookup` | Material Layer 그래프 익스포트 지원 (`get_material_function_expressions`) |
| 권장 조치 | `export-editor-metadata` 후 `unreal_asset_graph_lookup`으로 노드·와이어 확인 |

Material Function (`MF_*`)도 동일하게 **graph_material**입니다. Material Parameter Collection (`MPC_*`)은 스칼라/벡터 파라미터 기본값만 익스포트되며 노드 그래프는 없습니다.
