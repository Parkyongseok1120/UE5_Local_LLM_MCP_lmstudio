#pragma once
template<class T>
struct TSoftObjectPtr
{
	T* Get() const;
	T* LoadSynchronous() const;
	bool IsNull() const;
};
