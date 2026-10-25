#pragma once
struct FKey
{
	FName GetFName() const;
	bool IsValid() const;
};

struct EKeys
{
	static const FKey W;
	static const FKey SpaceBar;
};

enum EInputEvent : int
{
	IE_Pressed = 0,
	IE_Released = 1,
};
