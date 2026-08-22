#!/usr/bin/env python
"""Canonical Editor export kinds, raw files, and Unreal asset classes."""

METADATA_FILES = {
    "blueprint": "raw_blueprint_metadata.jsonl",
    "material": "raw_material_metadata.jsonl",
    "texture": "raw_texture_metadata.jsonl",
    "mesh": "raw_mesh_metadata.jsonl",
    "world_look": "raw_world_look_metadata.jsonl",
    "structured": "raw_structured_metadata.jsonl",
    "fmod": "raw_fmod_metadata.jsonl",
    "animation": "raw_animation_metadata.jsonl",
    "skeletal_mesh": "raw_skeletal_mesh_metadata.jsonl",
    "anim_blueprint": "raw_anim_blueprint_metadata.jsonl",
    "anim_montage": "raw_anim_montage_metadata.jsonl",
    "sequencer": "raw_sequencer_metadata.jsonl",
    "asset_registry": "raw_asset_registry.jsonl",
    "project_settings": "raw_project_settings.jsonl",
    "level": "raw_level_metadata.jsonl",
}

KIND_ASSET_TYPES = {
    "blueprint": {"Blueprint", "WidgetBlueprint", "AnimBlueprint"},
    "material": {
        "Material", "MaterialInstance", "MaterialInstanceConstant", "MaterialFunction",
        "MaterialFunctionMaterialLayer", "MaterialFunctionMaterialLayerBlend",
        "MaterialParameterCollection",
    },
    "structured": {
        "DataTable", "DataAsset", "PrimaryDataAsset", "CurveFloat", "CurveVector",
        "CurveLinearColor", "CurveTable", "StringTable", "DataRegistry", "UserDefinedEnum",
        "NiagaraSystem", "NiagaraEmitter", "NiagaraParameterCollection", "NiagaraScript",
        "ParticleSystem", "BehaviorTree", "BlackboardData", "EnvQuery",
        "SmartObjectDefinition", "NavModifierVolume", "SoundCue", "SoundWave",
        "MetaSoundSource", "MetaSoundPatch", "SoundClass", "SoundMix", "SoundAttenuation",
        "InputAction", "InputMappingContext", "InputModifier", "InputTrigger",
        "PhysicalMaterial", "GameplayAbility", "GameplayEffect", "GameplayCueNotify_Static",
        "Font", "FontFace", "MediaPlayer", "FileMediaSource", "ImgMediaSource",
    },
    "texture": {
        "Texture2D", "TextureCube", "TextureRenderTarget", "TextureRenderTarget2D",
        "MediaTexture", "RuntimeVirtualTexture",
    },
    "mesh": {"StaticMesh", "GeometryCollection", "FoliageType_InstancedStaticMesh"},
    "world_look": {
        "PostProcessVolume", "SkyAtmosphere", "ExponentialHeightFog", "VolumetricCloud",
        "DataLayerAsset",
    },
    "fmod": {"FMODEvent", "FMODBank", "FMODBankLookup", "FMODAsset"},
    "animation": {
        "SkeletalMesh", "AnimBlueprint", "AnimSequence", "AnimMontage", "AnimNotify",
        "AnimNotifyState", "LevelSequence", "PoseAsset", "BlendSpace", "BlendSpace1D",
        "AimOffsetBlendSpace", "Skeleton", "PhysicsAsset", "ControlRigBlueprint",
        "IKRigDefinition", "IKRetargeter",
    },
    "skeletal_mesh": {"SkeletalMesh"},
    "anim_blueprint": {"AnimBlueprint"},
    "anim_montage": {"AnimMontage"},
    "sequencer": {"LevelSequence"},
    "level": {"World", "Level"},
}

AGGREGATE_ANIMATION_ASSET_TYPES = {
    "skeletal_mesh": "SkeletalMesh",
    "anim_blueprint": "AnimBlueprint",
    "anim_montage": "AnimMontage",
    "sequencer": "LevelSequence",
}

__all__ = [
    "AGGREGATE_ANIMATION_ASSET_TYPES",
    "KIND_ASSET_TYPES",
    "METADATA_FILES",
]
