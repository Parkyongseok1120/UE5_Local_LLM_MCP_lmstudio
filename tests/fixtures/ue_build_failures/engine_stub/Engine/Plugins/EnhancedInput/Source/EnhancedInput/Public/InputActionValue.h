#pragma once

enum class EInputActionValueType : uint8
{
	Boolean,
	Axis1D,
	Axis2D,
	Axis3D,
};

struct FInputActionValue
{
	float GetMagnitude() const;
};
