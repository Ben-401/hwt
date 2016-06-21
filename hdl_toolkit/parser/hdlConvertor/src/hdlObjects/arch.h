#pragma once
#include <vector>
#include "named.h"
#include "compInstance.h"
#include "jsonable.h"

class Arch: public Named {
public:
	const char * entityName;
	std::vector<CompInstance*> componentInstances;

	PyObject * toJson() const;
};
