/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle_sl.cpp Code handling saving and loading of the used vehicle market and vehicle owner history. */

#include "../stdafx.h"
#include "../used_vehicle.h"

#include "saveload.h"

#include <map>

#include "../safeguards.h"

extern std::map<VehicleID, VehicleOwnerInfo> _vehicle_owner_info;

static const NamedSaveLoad _vehicle_owner_record_desc[] = {
	NSL("company",     SLE_VAR(VehicleOwnerRecord, company,     SLE_UINT8)),
	NSL("custom_name", SLE_SSTR(VehicleOwnerRecord, custom_name, SLE_STR | SLF_ALLOW_CONTROL)),
	NSL("name_1",      SLE_VAR(VehicleOwnerRecord, name_1,      SLE_UINT32)),
	NSL("name_2",      SLE_VAR(VehicleOwnerRecord, name_2,      SLE_UINT32)),
	NSL("start",       SLE_VAR(VehicleOwnerRecord, start,       SLE_INT32)),
	NSL("end",         SLE_VAR(VehicleOwnerRecord, end,         SLE_INT32)),
};

/** Owner history of a listing or a vehicle. */
template <typename T>
struct VehicleOwnerHistoryStructHandler final : public TypedSaveLoadStructHandler<VehicleOwnerHistoryStructHandler<T>, T> {
	NamedSaveLoadTable GetDescription() const override
	{
		return _vehicle_owner_record_desc;
	}

	void Save(T *object) const override
	{
		SlSetStructListLength(object->history.size());
		for (VehicleOwnerRecord &record : object->history) {
			SlObjectSaveFiltered(&record, this->GetLoadDescription());
		}
	}

	void Load(T *object) const override
	{
		object->history.resize(SlGetStructListLength(UINT32_MAX));
		for (VehicleOwnerRecord &record : object->history) {
			SlObjectLoadFiltered(&record, this->GetLoadDescription());
		}
	}
};

struct UsedVehicleListingStructHandler final : public TypedSaveLoadStructHandler<UsedVehicleListingStructHandler, UsedVehicleMarket> {
	NamedSaveLoadTable GetDescription() const override
	{
		static const NamedSaveLoad _used_vehicle_listing_desc[] = {
			NSL("id",     SLE_VAR(UsedVehicleListing, id,     SLE_UINT32)),
			NSL("engine", SLE_VAR(UsedVehicleListing, engine, SLE_UINT16)),
			NSL("age",    SLE_VAR(UsedVehicleListing, age,    SLE_INT32)),
			NSL("seller", SLE_VAR(UsedVehicleListing, seller, SLE_UINT8)),
			NSL("name",   SLE_SSTR(UsedVehicleListing, name,  SLE_STR | SLF_ALLOW_CONTROL)),
			NSLT_STRUCTLIST<VehicleOwnerHistoryStructHandler<UsedVehicleListing>>("history"),
		};
		return _used_vehicle_listing_desc;
	}

	void Save(UsedVehicleMarket *market) const override
	{
		SlSetStructListLength(market->listings.size());
		for (UsedVehicleListing &listing : market->listings) {
			SlObjectSaveFiltered(&listing, this->GetLoadDescription());
		}
	}

	void Load(UsedVehicleMarket *market) const override
	{
		market->listings.resize(SlGetStructListLength(UINT32_MAX));
		for (UsedVehicleListing &listing : market->listings) {
			SlObjectLoadFiltered(&listing, this->GetLoadDescription());
		}
	}
};

static const NamedSaveLoad _used_vehicle_market_desc[] = {
	NSL("seeded",  SLE_VAR(UsedVehicleMarket, seeded,  SLE_BOOL)),
	NSL("next_id", SLE_VAR(UsedVehicleMarket, next_id, SLE_UINT32)),
	NSLT_STRUCTLIST<UsedVehicleListingStructHandler>("listings"),
};

static void Save_UVMK()
{
	SlSaveTableObjectChunk(_used_vehicle_market_desc, &_used_vehicle_market);
}

static void Load_UVMK()
{
	_used_vehicle_market = {};
	SlLoadTableObjectChunk(_used_vehicle_market_desc, &_used_vehicle_market);
}

static const NamedSaveLoad _vehicle_owner_info_desc[] = {
	NSL("acquired", SLE_VAR(VehicleOwnerInfo, acquired, SLE_INT32)),
	NSLT_STRUCTLIST<VehicleOwnerHistoryStructHandler<VehicleOwnerInfo>>("history"),
};

static void Save_UVOH()
{
	SaveLoadTableData slt = SlTableHeader(_vehicle_owner_info_desc);

	for (auto &it : _vehicle_owner_info) {
		SlSetArrayIndex(it.first.base());
		SlObjectSaveFiltered(&it.second, slt);
	}
}

static void Load_UVOH()
{
	SaveLoadTableData slt = SlTableHeader(_vehicle_owner_info_desc);

	_vehicle_owner_info.clear();
	int index;
	while ((index = SlIterateArray()) != -1) {
		SlObjectLoadFiltered(&_vehicle_owner_info[VehicleID(index)], slt);
	}
}

static const ChunkHandler used_vehicle_chunk_handlers[] = {
	{ 'UVMK', Save_UVMK, Load_UVMK, nullptr, nullptr, ChunkType::Table },
	{ 'UVOH', Save_UVOH, Load_UVOH, nullptr, nullptr, ChunkType::Table },
};

extern const ChunkHandlerTable _used_vehicle_chunk_handlers(used_vehicle_chunk_handlers);
