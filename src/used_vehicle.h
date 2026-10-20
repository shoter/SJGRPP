/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle.h Used vehicle market: listings of second-hand vehicles and vehicle owner history. */

#ifndef USED_VEHICLE_H
#define USED_VEHICLE_H

#include "company_type.h"
#include "date_type.h"
#include "economy_type.h"
#include "engine_type.h"
#include "strings_type.h"
#include "tile_type.h"
#include "vehicle_type.h"

#include <string>
#include <vector>

/** One (former) owner of a vehicle. */
struct VehicleOwnerRecord {
	CompanyID company = CompanyID::Invalid(); ///< Company at the time, it may not exist any more or be reused.
	std::string custom_name;                  ///< Custom company name, empty when the generated name was used.
	StringID name_1 = STR_NULL;               ///< Generated company name string.
	uint32_t name_2 = 0;                      ///< Parameter of #name_1.
	CalTime::Date start{};                    ///< Date the company acquired the vehicle.
	CalTime::Date end{};                      ///< Date the company sold the vehicle.

	std::string GetCompanyName() const;
};

using VehicleOwnerHistory = std::vector<VehicleOwnerRecord>;

/** Owner history of a vehicle that has had other owners. */
struct VehicleOwnerInfo {
	CalTime::Date acquired{};    ///< Date the current owner acquired the vehicle.
	VehicleOwnerHistory history; ///< Previous owners, oldest first.
};

/** A used vehicle offered on the market. */
struct UsedVehicleListing {
	uint32_t id = 0;                         ///< Unique identifier of this listing.
	EngineID engine = EngineID::Invalid();   ///< Engine type of the vehicle.
	CalTime::DateDelta age{};                ///< Age of the vehicle in days.
	CompanyID seller = CompanyID::Invalid(); ///< Company that sold the vehicle, invalid for generated listings.
	std::string name;                        ///< Custom name of the vehicle, if any.
	VehicleOwnerHistory history;             ///< Previous owners, oldest first.

	Money GetPrice() const;
	uint16_t GetReliability() const;
};

struct UsedVehicleMarket {
	bool seeded = false;                      ///< Whether the initial listings were generated.
	uint32_t next_id = 1;                     ///< Identifier of the next listing.
	std::vector<UsedVehicleListing> listings; ///< All listings.
};

extern UsedVehicleMarket _used_vehicle_market;

bool IsUsedVehicleMarketEnabled();
bool IsUsedVehicleMarketEngine(EngineID engine);
bool IsUsedEngineBuildable(EngineID engine, VehicleType type, CompanyID company);
UsedVehicleListing *GetUsedVehicleListing(uint32_t id);
void RemoveUsedVehicleListing(uint32_t id);

int GetUsedVehicleMaxAgeYears(CalTime::DateDelta life_length, CalTime::DateDelta since_intro);

void InitializeUsedVehicleMarket();
void UsedVehicleMarketDailyLoop();
void UsedVehicleMarketYearlyLoop();

const VehicleOwnerInfo *GetVehicleOwnerInfo(VehicleID veh);
VehicleOwnerInfo &GetOrCreateVehicleOwnerInfo(VehicleID veh);
void RemoveVehicleOwnerInfo(VehicleID veh);
CalTime::Date GetVehicleAcquiredDate(const struct Vehicle *v);
VehicleOwnerRecord MakeVehicleOwnerRecord(CompanyID company, CalTime::Date start, CalTime::Date end);
void RecordVehicleOwnerChange(struct Vehicle *v, CompanyID new_owner);

/** Data of a vehicle collected before selling it to the market. */
struct UsedVehicleSaleCandidate {
	EngineID engine;
	CalTime::DateDelta age;
	CompanyID seller;
	std::string name;
	VehicleOwnerHistory history;
};

void CollectUsedVehicleSaleCandidates(const struct Vehicle *v, bool sell_chain, std::vector<UsedVehicleSaleCandidate> &out);
void AddUsedVehicleListings(std::vector<UsedVehicleSaleCandidate> &&candidates);

void InvalidateUsedVehicleWindows();
void ShowBuyUsedVehicleWindow(TileIndex tile, VehicleType type);

#endif /* USED_VEHICLE_H */
