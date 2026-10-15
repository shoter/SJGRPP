/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/**
 * @file debug_bridge_reflect.cpp Generic object inspection for the debug bridge.
 *
 * Reuses the named savegame descriptor tables: every field that is saved can be read by name,
 * without having to write any per-field code.
 * Sub-structures (SL_STRUCT/SL_STRUCTLIST) are not descended into, as their descriptions
 * are relative to other objects; they are listed by name only.
 */

#include "stdafx.h"
#include "company_base.h"
#include "industry.h"
#include "order_base.h"
#include "station_base.h"
#include "town.h"
#include "vehicle_base.h"
#include "waypoint_base.h"
#include "sl/saveload.h"

#include "3rdparty/nlohmann/json.hpp"

#include "safeguards.h"

NamedSaveLoadTable GetCompanyDescriptionForDebug();
NamedSaveLoadTable GetTownDescriptionForDebug();
NamedSaveLoadTable GetBaseStationDescriptionForDebug();
NamedSaveLoadTable GetStationDescriptionForDebug();
NamedSaveLoadTable GetWaypointDescriptionForDebug();
NamedSaveLoadTable GetIndustryDescriptionForDebug();
NamedSaveLoadTable GetDateDescriptionForDebug();

using json = nlohmann::json;

/** A descriptor table together with the address its (non-global) offsets are relative to. */
struct ReflectPart {
	NamedSaveLoadTable table;
	const void *base;
};

static size_t MemTypeSize(VarMemType mem)
{
	switch (mem) {
		case SLE_VAR_BL:
		case SLE_VAR_I8:
		case SLE_VAR_U8:
			return 1;
		case SLE_VAR_I16:
		case SLE_VAR_U16:
			return 2;
		case SLE_VAR_I32:
		case SLE_VAR_U32:
		case SLE_VAR_LABEL:
			return 4;
		case SLE_VAR_I64:
		case SLE_VAR_U64:
			return 8;
		default:
			return 0;
	}
}

static json ReadScalar(const void *ptr, VarMemType mem)
{
	switch (mem) {
		case SLE_VAR_BL:
			return *static_cast<const bool *>(ptr);
		case SLE_VAR_U64:
			return *static_cast<const uint64_t *>(ptr);
		case SLE_VAR_LABEL: {
			uint32_t label = *static_cast<const uint32_t *>(ptr);
			std::string str;
			for (int shift = 24; shift >= 0; shift -= 8) {
				char c = static_cast<char>(label >> shift);
				str += (c >= 0x20 && c < 0x7F) ? c : '?';
			}
			return str;
		}
		default:
			if (MemTypeSize(mem) == 0) return nullptr;
			return ReadValue(ptr, mem);
	}
}

static json ReadReference(const void *target, SLRefType rt)
{
	if (target == nullptr) return nullptr;
	switch (rt) {
		case REF_VEHICLE_OLD:
		case REF_VEHICLE:   return json{ { "ref", "vehicle" }, { "id", static_cast<const Vehicle *>(target)->index.base() } };
		case REF_STATION:   return json{ { "ref", "station" }, { "id", static_cast<const Station *>(target)->index.base() } };
		case REF_TOWN:      return json{ { "ref", "town" }, { "id", static_cast<const Town *>(target)->index.base() } };
		case REF_ORDERLIST: return json{ { "ref", "orderlist" }, { "id", static_cast<const OrderList *>(target)->index.base() } };
		default:            return json{ { "ref", static_cast<int>(rt) }, { "ptr", fmt::format("{}", target) } };
	}
}

static const char *SaveLoadTypeName(SaveLoadType cmd)
{
	switch (cmd) {
		case SL_VAR: return "var";
		case SL_REF: return "ref";
		case SL_ARR: return "array";
		case SL_STR: return "str";
		case SL_REFLIST: return "reflist";
		case SL_RING: return "ring";
		case SL_REFVEC: return "refvec";
		case SL_STDSTR: return "stdstr";
		case SL_REFRING: return "refring";
		case SL_VARVEC: return "varvec";
		case SL_CUSTOMLIST: return "customlist";
		case SL_STRUCT: return "struct";
		case SL_STRUCTLIST: return "structlist";
		default: return "other";
	}
}

/**
 * Read one descriptor entry.
 * @return The value, or a placeholder string describing the (unsupported) type.
 */
static json ReadEntry(const void *base, const SaveLoad &sld)
{
	switch (sld.cmd) {
		case SL_VAR: {
			const void *ptr = GetVariableAddress(base, sld);
			if (ptr == nullptr) return nullptr;
			return ReadScalar(ptr, sld.conv.mem);
		}

		case SL_REF:
			return ReadReference(*static_cast<void * const *>(GetVariableAddress(base, sld)), sld.conv.ref);

		case SL_ARR: {
			const uint8_t *ptr = static_cast<const uint8_t *>(GetVariableAddress(base, sld));
			size_t size = MemTypeSize(sld.conv.mem);
			if (ptr == nullptr || size == 0) break;
			json arr = json::array();
			for (uint i = 0; i < sld.length; i++) arr.push_back(ReadScalar(ptr + i * size, sld.conv.mem));
			return arr;
		}

		case SL_STR: {
			const char *str = *static_cast<const char * const *>(GetVariableAddress(base, sld));
			if (str == nullptr) return nullptr;
			return std::string(str);
		}

		case SL_STDSTR:
			/* Either std::string or EncodedString, which wraps a single std::string. */
			return *static_cast<const std::string *>(GetVariableAddress(base, sld));

		default:
			break;
	}
	return fmt::format("<{}>", SaveLoadTypeName(sld.cmd));
}

/** Get the descriptor parts for an object, or an empty vector if there is no such object. */
static std::vector<ReflectPart> GetReflectParts(std::string_view type, size_t id, std::string &error)
{
	if (type == "vehicle") {
		const Vehicle *v = Vehicle::GetIfValid(id);
		if (v == nullptr) { error = "no such vehicle"; return {}; }
		std::vector<ReflectPart> parts;
		if (v->type <= VehicleType::Aircraft) parts.push_back({ GetVehicleDescription(VehicleType::End), v });
		parts.push_back({ GetVehicleDescription(v->type), v });
		return parts;
	}
	if (type == "company") {
		const Company *c = Company::GetIfValid(id);
		if (c == nullptr) { error = "no such company"; return {}; }
		return { { GetCompanyDescriptionForDebug(), static_cast<const CompanyProperties *>(c) } };
	}
	if (type == "town") {
		const Town *t = Town::GetIfValid(id);
		if (t == nullptr) { error = "no such town"; return {}; }
		return { { GetTownDescriptionForDebug(), t } };
	}
	if (type == "station") {
		const BaseStation *bst = BaseStation::GetIfValid(id);
		if (bst == nullptr) { error = "no such station"; return {}; }
		if (Station::IsExpected(bst)) {
			return { { GetBaseStationDescriptionForDebug(), bst }, { GetStationDescriptionForDebug(), Station::From(bst) } };
		}
		return { { GetBaseStationDescriptionForDebug(), bst }, { GetWaypointDescriptionForDebug(), Waypoint::From(bst) } };
	}
	if (type == "industry") {
		const Industry *ind = Industry::GetIfValid(id);
		if (ind == nullptr) { error = "no such industry"; return {}; }
		return { { GetIndustryDescriptionForDebug(), ind } };
	}
	if (type == "globals") {
		return { { GetDateDescriptionForDebug(), nullptr } };
	}
	error = "unknown object type";
	return {};
}

json DebugBridgeReflectTypes()
{
	return json::array({ "vehicle", "company", "town", "station", "industry", "globals" });
}

/**
 * Inspect an object.
 * @param type Object type, see #DebugBridgeReflectTypes.
 * @param id Pool index of the object (ignored for "globals").
 * @param fields Fields to read; all fields if empty.
 * @param schema Whether to output the field types instead of values.
 * @param[out] error Error message on failure.
 * @return Object of field name to value, or null on failure.
 */
json DebugBridgeReflectObject(std::string_view type, size_t id, const std::vector<std::string> &fields, bool schema, std::string &error)
{
	std::vector<ReflectPart> parts = GetReflectParts(type, id, error);
	if (parts.empty()) return nullptr;

	json result = json::object();
	for (const ReflectPart &part : parts) {
		for (const NamedSaveLoad &nsld : part.table) {
			const SaveLoad &sld = nsld.save_load;
			if (nsld.name == nullptr || nsld.name[0] == '\0') continue;
			if (sld.cmd == SL_INCLUDE || sld.cmd == SL_WRITEBYTE) continue;
			if (!SlIsObjectCurrentlyValid(sld.version_from, sld.version_to, sld.ext_feature_test)) continue;
			if (!sld.global && part.base == nullptr) continue;
			if (!fields.empty() && std::find(fields.begin(), fields.end(), nsld.name) == fields.end()) continue;

			if (schema) {
				result[nsld.name] = SaveLoadTypeName(sld.cmd);
			} else {
				result[nsld.name] = ReadEntry(part.base, sld);
			}
		}
	}

	for (const std::string &field : fields) {
		if (!result.contains(field)) result[field] = "<no such field>";
	}
	return result;
}

/**
 * List the IDs of all objects of a type.
 * @param type Object type.
 * @param all For vehicles: include non-primary vehicles (articulated parts, wagons, effects...).
 * @param[out] error Error message on failure.
 */
json DebugBridgeReflectList(std::string_view type, bool all, std::string &error)
{
	json ids = json::array();
	if (type == "vehicle") {
		for (const Vehicle *v : Vehicle::Iterate()) {
			if (all || v->IsPrimaryVehicle()) ids.push_back(v->index.base());
		}
	} else if (type == "company") {
		for (const Company *c : Company::Iterate()) ids.push_back(c->index.base());
	} else if (type == "town") {
		for (const Town *t : Town::Iterate()) ids.push_back(t->index.base());
	} else if (type == "station") {
		for (const BaseStation *bst : BaseStation::Iterate()) ids.push_back(bst->index.base());
	} else if (type == "industry") {
		for (const Industry *ind : Industry::Iterate()) ids.push_back(ind->index.base());
	} else if (type == "globals") {
		ids.push_back(0);
	} else {
		error = "unknown object type";
		return nullptr;
	}
	return ids;
}
