/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file overseas.h Overseas stations: stations at the map border that ships and aircraft use to trade with other countries. */

#ifndef OVERSEAS_H
#define OVERSEAS_H

#include "cargo_type.h"
#include "company_type.h"
#include "economy_type.h"
#include "station_type.h"
#include "strings_type.h"
#include "tile_type.h"
#include "vehicle_type.h"

#include <array>
#include <span>
#include <string_view>
#include <vector>

/** A country an overseas station trades with. */
struct OverseasCountry {
	StringID name;           ///< Name of the country.
	uint16_t population;     ///< Population in millions, scales the production.
	uint16_t distance_km;    ///< Distance from the home country (Poland), sets the trip time.
	std::string_view flag;   ///< Drawing instructions of the flag, see overseas_flag.cpp.
};

/** Number of countries overseas stations can trade with. */
static constexpr uint NUM_OVERSEAS_COUNTRIES = 60;

/** Kinds of cargo an overseas station produces. */
enum class OverseasProduct : uint8_t {
	Passengers, ///< Passengers.
	Mail,       ///< Mail.
	Cargo,      ///< The freight cargo of the station.
	End,
};

/** Number of kinds of cargo an overseas station produces. */
static constexpr uint NUM_OVERSEAS_PRODUCTS = static_cast<uint>(OverseasProduct::End);

/** Fixed point unit of #OverseasStation::growth. */
static constexpr uint32_t OVERSEAS_GROWTH_UNIT = 1 << 16;

/** Overseas data of a station. */
struct OverseasStation {
	uint8_t country = 0;                                 ///< Index of the country in the country table.
	CargoType cargo = INVALID_CARGO;                     ///< Freight cargo produced by the station.
	CargoType accepted_cargo = INVALID_CARGO;            ///< Freight cargo accepted by the station.
	uint32_t growth = OVERSEAS_GROWTH_UNIT;              ///< Production multiplier from yearly growth, #OVERSEAS_GROWTH_UNIT is 1.
	std::array<uint32_t, NUM_OVERSEAS_PRODUCTS> fract{}; ///< Production not yet added to the station, in 1/#DAYS_IN_YEAR units.
};

std::span<const OverseasCountry> GetOverseasCountries();
const OverseasCountry &GetOverseasCountry(uint8_t country);

/* Pure calculations, also used by the unit tests. */
uint GetOverseasTripDaysForDistance(uint distance_km, uint min_km, uint max_km, uint min_days, uint max_days);
uint GetOverseasAircraftTripDays(uint ship_days, uint percent);
uint GetOverseasVirtualDistance(uint distance_km);
Money GetOverseasControlBasePrice(Money fee, uint owned);
uint GetOverseasPopulationScale(uint population);
uint GetOverseasMonthlyProduction(uint base, uint population, uint32_t growth, bool recession);
uint32_t GrowOverseasProduction(uint32_t growth, uint percent);
uint GetOverseasStockpileLimit(uint monthly, uint months);

/* Game state. */
struct Station;
struct Vehicle;
struct CargoPacket;

uint GetOverseasTripDays(const Station *st, VehicleType type);
uint GetOverseasTripTicks(const Station *st, VehicleType type);
uint GetOverseasStationVirtualDistance(const Station *st);
CargoType GetOverseasProductCargo(const Station *st, OverseasProduct product);
uint GetOverseasStationMonthlyProduction(const Station *st, OverseasProduct product);
uint GetOverseasStationStockpileLimit(const Station *st, OverseasProduct product);
uint CountOverseasStationsOf(CompanyID company);
Money GetOverseasControlPrice(CompanyID company);
StringID GetOverseasControlError(const Station *st, CompanyID company);
bool CanCompanyUseOverseasStation(const Station *st, Owner owner);
uint GetOverseasDistanceBonus(const CargoPacket *cp);
std::span<const StationID> GetOverseasLoadNextHops(std::span<const StationID> next, const class StationCargoList &list, std::vector<StationID> &buffer);

void GenerateOverseasStations();
void OverseasStationsDailyLoop();
void OverseasStationsYearlyLoop();
void UpdateOverseasAcceptance(Station *st);
void AfterLoadOverseasStations();

bool OverseasVehicleDepart(Vehicle *v, Station *st);
void OverseasVehicleTick(Vehicle *v);
uint GetOverseasDaysLeft(const Vehicle *v);
void OnOverseasVehicleDeleted(Vehicle *v);
void OnOverseasStationsCompanyChange(CompanyID old_owner, CompanyID new_owner);

bool DrawOverseasFlag(uint8_t country, const struct Rect &r);
void ClearOverseasFlagCache();

#endif /* OVERSEAS_H */
