/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file overseas.cpp Overseas stations: stations at the map border that ships and aircraft use to trade with other countries. */

#include "stdafx.h"
#include "overseas.h"
#include "overseas_cmd.h"
#include "aircraft.h"
#include "cargopacket.h"
#include "car_ferry.h"
#include "cargotype.h"
#include "command_func.h"
#include "company_base.h"
#include "company_func.h"
#include "date_func.h"
#include "economy_base.h"
#include "economy_func.h"
#include "industrytype.h"
#include "linkgraph/linkgraph_type.h"
#include "map_func.h"
#include "news_func.h"
#include "order_base.h"
#include "ship.h"
#include "station_base.h"
#include "station_func.h"
#include "station_kdtree.h"
#include "station_map.h"
#include "strings_func.h"
#include "town.h"
#include "vehicle_base.h"
#include "vehicle_func.h"
#include "vehicle_gui.h"
#include "water_map.h"
#include "window_func.h"
#include "core/random_func.hpp"
#include "core/format.hpp"
#include "debug_bridge.h"
#include "table/strings.h"

#include <cmath>

#include "safeguards.h"

/**
 * The 60 most populous countries after Poland, which is the home country. Population in millions (about 2024),
 * distance in km between the capital and Warsaw.
 */
static const std::array<OverseasCountry, NUM_OVERSEAS_COUNTRIES> _overseas_countries = {{
	{ STR_OVERSEAS_COUNTRY_INDIA,         1441,  5240, "H FF9933 FFFFFF 138808; C 60 40 10 000080; C 60 40 8 FFFFFF; C 60 40 2.5 000080" },
	{ STR_OVERSEAS_COUNTRY_CHINA,         1419,  6960, "R 0 0 120 80 DE2910; S 20 20 12 FFDE00; S 40 8 4 FFDE00; S 48 16 4 FFDE00; S 48 28 4 FFDE00; S 40 36 4 FFDE00" },
	{ STR_OVERSEAS_COUNTRY_USA,            342,  7200, "H B22234 FFFFFF B22234 FFFFFF B22234 FFFFFF B22234 FFFFFF B22234 FFFFFF B22234 FFFFFF B22234; R 0 0 48 43 3C3B6E; G 0 0 48 43 6 5 1.6 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_INDONESIA,      281, 10200, "H FF0000 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_PAKISTAN,       251,  4500, "R 0 0 30 80 FFFFFF; R 30 0 120 80 01411C; M 75 40 22 7 19 FFFFFF; S 88 30 7 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_NIGERIA,        233,  4900, "V 008751 FFFFFF 008751" },
	{ STR_OVERSEAS_COUNTRY_BRAZIL,         212,  9800, "R 0 0 120 80 009C3B; Q 60 7 113 40 60 73 7 40 FFDF00; C 60 40 17 002776; L 44 36 76 44 3 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_BANGLADESH,     174,  6560, "R 0 0 120 80 006A4E; C 54 40 22 F42A41" },
	{ STR_OVERSEAS_COUNTRY_RUSSIA,         144,  1150, "H FFFFFF 0039A6 D52B1E" },
	{ STR_OVERSEAS_COUNTRY_ETHIOPIA,       132,  5400, "H 078930 FCDD09 DA121A; C 60 40 18 0F47AF; S 60 41 13 FCDD09" },
	{ STR_OVERSEAS_COUNTRY_MEXICO,         131, 10400, "V 006847 FFFFFF CE1126; E 60 40 9 10 8C5A2B; CB 60 44 9 006847" },
	{ STR_OVERSEAS_COUNTRY_JAPAN,          124,  8580, "R 0 0 120 80 FFFFFF; C 60 40 24 BC002D" },
	{ STR_OVERSEAS_COUNTRY_PHILIPPINES,    116,  9300, "H 0038A8 CE1126; T 0 0 69 40 0 80 FFFFFF; C 23 40 8 FCD116; S 7 9 4 FCD116; S 7 71 4 FCD116; S 55 40 4 FCD116" },
	{ STR_OVERSEAS_COUNTRY_EGYPT,          116,  2600, "H CE1126 FFFFFF 000000; E 60 40 7 9 C09300" },
	{ STR_OVERSEAS_COUNTRY_DR_CONGO,       109,  6300, "R 0 0 120 80 007FFF; L -10 90 130 -10 30 F7D618; L -10 90 130 -10 21 CE1021; S 22 18 13 F7D618" },
	{ STR_OVERSEAS_COUNTRY_VIETNAM,        101,  8000, "R 0 0 120 80 DA251D; S 60 42 26 FFFF00" },
	{ STR_OVERSEAS_COUNTRY_IRAN,            91,  3000, "H 239F40 FFFFFF DA0000; E 60 40 6 9 DA0000; E 60 40 3 7 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_TURKEY,          87,  1800, "R 0 0 120 80 E30A17; M 45 40 20 5 16 FFFFFF; S 70 40 10 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_GERMANY,         84,   520, "H 000000 DD0000 FFCE00" },
	{ STR_OVERSEAS_COUNTRY_THAILAND,        72,  8000, "H A51931 F4F5F8 2:2D2A4A F4F5F8 A51931" },
	{ STR_OVERSEAS_COUNTRY_TANZANIA,        68,  7000, "T 0 0 120 0 0 80 1EB53A; T 120 0 120 80 0 80 00A3DD; L -10 90 130 -10 30 FCD116; L -10 90 130 -10 21 000000" },
	{ STR_OVERSEAS_COUNTRY_UK,              69,  1450, "R 0 0 120 80 012169; L 0 0 120 80 16 FFFFFF; L 0 80 120 0 16 FFFFFF; L 0 0 120 80 6 C8102E; L 0 80 120 0 6 C8102E; R 0 27 120 53 FFFFFF; R 47 0 73 80 FFFFFF; R 0 32 120 48 C8102E; R 52 0 68 80 C8102E" },
	{ STR_OVERSEAS_COUNTRY_FRANCE,          66,  1370, "V 0055A4 FFFFFF EF4135" },
	{ STR_OVERSEAS_COUNTRY_SOUTH_AFRICA,    64,  8850, "H E03C31 001489; T 0 0 64 40 0 80 FFFFFF; R 56 29 120 51 FFFFFF; T 0 8 52 40 0 72 007749; R 50 34 120 46 007749; T 0 16 39 40 0 64 FFB81C; T 0 22 30 40 0 58 000000" },
	{ STR_OVERSEAS_COUNTRY_ITALY,           59,  1320, "V 009246 FFFFFF CE2B37" },
	{ STR_OVERSEAS_COUNTRY_KENYA,           56,  6250, "H 6:000000 FFFFFF 6:BB0000 FFFFFF 6:006600; E 60 40 9 22 BB0000; E 60 40 3 14 000000" },
	{ STR_OVERSEAS_COUNTRY_MYANMAR,         54,  7300, "H FECB00 34B233 EA2839; S 60 44 26 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_COLOMBIA,        53,  9700, "H 2:FCD116 003893 CE1126" },
	{ STR_OVERSEAS_COUNTRY_SOUTH_KOREA,     52,  7700, "R 0 0 120 80 FFFFFF; C 60 40 18 CD2E3A; CB 60 40 18 0047A0; L 18 14 32 28 4 000000; L 88 52 102 66 4 000000; L 88 28 102 14 4 000000; L 18 66 32 52 4 000000" },
	{ STR_OVERSEAS_COUNTRY_UGANDA,          50,  5950, "H 000000 FCDC04 D90000 000000 FCDC04 D90000; C 60 40 14 FFFFFF; E 60 40 5 8 777777" },
	{ STR_OVERSEAS_COUNTRY_SUDAN,           50,  4100, "H D21034 FFFFFF 000000; T 0 0 42 40 0 80 007229" },
	{ STR_OVERSEAS_COUNTRY_SPAIN,           48,  2300, "H AA151B 2:F1BF00 AA151B; R 28 30 40 50 AD1519; R 30 32 38 40 F1BF00" },
	{ STR_OVERSEAS_COUNTRY_ALGERIA,         47,  2000, "V 006233 FFFFFF; M 60 40 20 6 16 D21034; S 72 40 9 D21034" },
	{ STR_OVERSEAS_COUNTRY_IRAQ,            46,  2800, "H CE1126 FFFFFF 000000; R 36 37 84 43 007A3D" },
	{ STR_OVERSEAS_COUNTRY_ARGENTINA,       46, 12300, "H 74ACDF FFFFFF 74ACDF; S 60 40 10 F6B40E 16; C 60 40 6 F6B40E" },
	{ STR_OVERSEAS_COUNTRY_AFGHANISTAN,     43,  4100, "V 000000 D32011 007A36; C 60 40 13 FFFFFF; C 60 40 10 D32011" },
	{ STR_OVERSEAS_COUNTRY_CANADA,          40,  6800, "V D80621 2:FFFFFF D80621; S 60 38 20 D80621 7; R 58 48 62 62 D80621" },
	{ STR_OVERSEAS_COUNTRY_UKRAINE,         37,   690, "H 0057B7 FFD700" },
	{ STR_OVERSEAS_COUNTRY_MOROCCO,         38,  2850, "R 0 0 120 80 C1272D; S 60 42 18 006233" },
	{ STR_OVERSEAS_COUNTRY_SAUDI_ARABIA,    34,  3700, "R 0 0 120 80 006C35; R 30 26 90 38 FFFFFF; R 34 46 86 50 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_ANGOLA,          37,  6700, "H CC092F 000000; M 60 40 15 -5 12 FFCB00; S 64 32 6 FFCB00; L 50 52 72 36 3 FFCB00" },
	{ STR_OVERSEAS_COUNTRY_UZBEKISTAN,      37,  3500, "H 10:0099B5 CE1126 10:FFFFFF CE1126 10:1EB53A; M 20 13 9 4 8 FFFFFF; G 34 4 58 22 4 3 1.6 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_YEMEN,           40,  4700, "H CE1126 FFFFFF 000000" },
	{ STR_OVERSEAS_COUNTRY_PERU,            34, 11400, "V D91023 FFFFFF D91023" },
	{ STR_OVERSEAS_COUNTRY_MOZAMBIQUE,      34,  8700, "H 6:007168 FFFFFF 6:000000 FFFFFF 6:FCE100; T 0 0 52 40 0 80 D21034; S 18 40 11 FCE100" },
	{ STR_OVERSEAS_COUNTRY_GHANA,           34,  5300, "H CE1126 FCD116 006B3F; S 60 41 12 000000" },
	{ STR_OVERSEAS_COUNTRY_MALAYSIA,        35,  8900, "H CC0001 FFFFFF CC0001 FFFFFF CC0001 FFFFFF CC0001 FFFFFF CC0001 FFFFFF CC0001 FFFFFF CC0001 FFFFFF; R 0 0 60 46 010066; M 22 23 15 5 12 FFCC00; S 42 23 11 FFCC00 14" },
	{ STR_OVERSEAS_COUNTRY_NEPAL,           31,  5900, "T 4 2 96 40 4 40 003893; T 4 30 96 78 4 78 003893; T 9 10 80 37 9 37 DC143C; T 9 39 80 73 9 73 DC143C; M 26 24 7 0 0 FFFFFF; S 26 28 6 FFFFFF 12; S 26 61 8 FFFFFF 12" },
	{ STR_OVERSEAS_COUNTRY_MADAGASCAR,      31,  8300, "R 0 0 40 80 FFFFFF; R 40 0 120 40 FC3D32; R 40 40 120 80 007E3A" },
	{ STR_OVERSEAS_COUNTRY_VENEZUELA,       28,  8900, "H FFCC00 00247D CF142B; G 36 30 84 40 8 1 2 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_IVORY_COAST,     31,  5400, "V F77F00 FFFFFF 009E60" },
	{ STR_OVERSEAS_COUNTRY_CAMEROON,        29,  5200, "V 007A5E CE1126 FCD116; S 60 41 11 FCD116" },
	{ STR_OVERSEAS_COUNTRY_NIGER,           27,  4300, "H E05206 FFFFFF 0DB02B; C 60 40 9 E05206" },
	{ STR_OVERSEAS_COUNTRY_AUSTRALIA,       27, 15800, "R 0 0 120 80 012169; L 0 0 60 40 8 FFFFFF; L 0 40 60 0 8 FFFFFF; L 0 0 60 40 3 C8102E; L 0 40 60 0 3 C8102E; R 0 14 60 26 FFFFFF; R 24 0 36 40 FFFFFF; R 0 17 60 23 C8102E; R 27 0 33 40 C8102E; S 30 60 10 FFFFFF 7; S 90 18 5 FFFFFF 7; S 104 36 5 FFFFFF 7; S 90 66 5 FFFFFF 7; S 78 40 5 FFFFFF 7; S 98 48 3 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_NORTH_KOREA,     26,  7500, "H 3:024FA2 FFFFFF 12:ED1C27 FFFFFF 3:024FA2; C 40 40 13 FFFFFF; S 40 41 12 ED1C27" },
	{ STR_OVERSEAS_COUNTRY_TAIWAN,          23,  8700, "R 0 0 120 80 FE0000; R 0 0 60 40 000095; S 30 20 15 FFFFFF 12; C 30 20 8 000095; C 30 20 7 FFFFFF" },
	{ STR_OVERSEAS_COUNTRY_MALI,            24,  4800, "V 14B53A FCD116 CE1126" },
	{ STR_OVERSEAS_COUNTRY_SYRIA,           24,  2600, "H 007A3D FFFFFF 000000; S 36 41 7 CE1126; S 60 41 7 CE1126; S 84 41 7 CE1126" },
	{ STR_OVERSEAS_COUNTRY_BURKINA_FASO,    23,  4800, "H EF2B2D 009E49; S 60 41 13 FCD116" },
	{ STR_OVERSEAS_COUNTRY_SRI_LANKA,       22,  7000, "R 0 0 120 80 FFBE29; R 4 4 20 76 00534E; R 20 4 36 76 FF7300; R 40 4 116 76 8D153A; E 78 40 14 16 FFBE29; E 78 40 9 11 8D153A" },
}};

/**
 * Overseas stations for the debug bridge: id:country:owner:cargo:accepted_cargo:growth, then waiting:monthly per product,
 * then the number of cargoes for which the station is in a link graph.
 */
static std::string DumpOverseasStations()
{
	format_buffer buf;
	for (const Station *st : Station::Iterate()) {
		if (st->overseas == nullptr) continue;
		buf.format("{}:{}:{}:{}:{}:{}", st->index.base(), st->overseas->country, st->owner.base(), static_cast<uint>(st->overseas->cargo), static_cast<uint>(st->overseas->accepted_cargo), st->overseas->growth);
		for (uint p = 0; p < NUM_OVERSEAS_PRODUCTS; p++) {
			const CargoType cargo = GetOverseasProductCargo(st, static_cast<OverseasProduct>(p));
			const uint waiting = IsValidCargoType(cargo) ? st->goods[cargo].CargoTotalCount() : 0;
			buf.format(":{}:{}", waiting, GetOverseasStationMonthlyProduction(st, static_cast<OverseasProduct>(p)));
		}
		uint link_graphs = 0;
		for (const GoodsEntry &ge : st->goods) {
			if (ge.link_graph != LinkGraphID::Invalid()) link_graphs++;
		}
		buf.format(":{};", link_graphs);
	}
	return buf.to_string();
}

/** Cargo of ships and aircraft for the debug bridge: id:amount:periods in transit summed over the cargo. */
static std::string DumpOverseasVehicleCargo()
{
	format_buffer buf;
	for (const Vehicle *v : Vehicle::Iterate()) {
		if (!v->IsPrimaryVehicle() || (v->type != VehicleType::Ship && v->type != VehicleType::Aircraft)) continue;
		uint count = 0;
		uint64_t periods = 0;
		for (const Vehicle *u = v; u != nullptr; u = u->Next()) {
			count += u->cargo.StoredCount();
			periods += u->cargo.CargoPeriodsInTransit();
		}
		buf.format("{}:{}:{};", v->index.base(), count, periods);
	}
	return buf.to_string();
}

DEBUG_BRIDGE_WATCH("overseas.stations", DumpOverseasStations());
DEBUG_BRIDGE_WATCH("overseas.vehicle_cargo", DumpOverseasVehicleCargo());

std::span<const OverseasCountry> GetOverseasCountries()
{
	return _overseas_countries;
}

const OverseasCountry &GetOverseasCountry(uint8_t country)
{
	assert(country < NUM_OVERSEAS_COUNTRIES);
	return _overseas_countries[country];
}

/**
 * Get the time a ship needs for a trip to a country and back.
 * The nearest country takes the shortest time, the farthest one the longest time, the others in between by distance.
 * @param distance_km Distance of the country.
 * @param min_km Distance of the nearest country.
 * @param max_km Distance of the farthest country.
 * @param min_days Shortest trip.
 * @param max_days Longest trip.
 * @return The trip time in days.
 */
uint GetOverseasTripDaysForDistance(uint distance_km, uint min_km, uint max_km, uint min_days, uint max_days)
{
	if (max_days < min_days) std::swap(min_days, max_days);
	if (max_km <= min_km) return min_days;
	distance_km = std::clamp(distance_km, min_km, max_km);
	return min_days + ((distance_km - min_km) * (max_days - min_days) + (max_km - min_km) / 2) / (max_km - min_km);
}

/**
 * Get the time an aircraft needs for a trip to a country and back.
 * @param ship_days Trip time of a ship.
 * @param percent Aircraft trip time in percent of the ship trip time.
 * @return The trip time in days, at least one day.
 */
uint GetOverseasAircraftTripDays(uint ship_days, uint percent)
{
	return std::max<uint>(1, (ship_days * percent + 50) / 100);
}

/**
 * Get the distance cargo is paid for between the overseas station and its country.
 * @param distance_km Distance of the country.
 * @return The distance in tiles.
 */
uint GetOverseasVirtualDistance(uint distance_km)
{
	return std::max<uint>(8, distance_km / 50);
}

/**
 * Get the price for taking control of an overseas station, before inflation.
 * @param fee Price of the first overseas station.
 * @param owned Number of overseas stations the company controls.
 * @return fee * (owned + 1)!
 */
Money GetOverseasControlBasePrice(Money fee, uint owned)
{
	Money price = fee;
	for (uint i = 2; i <= owned + 1; i++) price *= static_cast<int64_t>(i);
	return price;
}

/**
 * Get the production scale of a country: the square root of the population relative to 100 million, from 0.5 to 4.
 * @param population Population in millions.
 * @return Scale, 256 is 1.
 */
uint GetOverseasPopulationScale(uint population)
{
	uint scale = static_cast<uint>(std::lround(std::sqrt(population / 100.0) * 256));
	return std::clamp<uint>(scale, 128, 1024);
}

/**
 * Get the monthly production of a cargo of an overseas station.
 * @param base Production setting.
 * @param population Population of the country in millions.
 * @param growth Growth multiplier, #OVERSEAS_GROWTH_UNIT is 1.
 * @param recession Whether the economy is in recession, which halves the production.
 * @return The amount produced per month.
 */
uint GetOverseasMonthlyProduction(uint base, uint population, uint32_t growth, bool recession)
{
	uint64_t amount = static_cast<uint64_t>(base) * GetOverseasPopulationScale(population) * growth;
	amount = (amount + (static_cast<uint64_t>(256) * OVERSEAS_GROWTH_UNIT) / 2) / (static_cast<uint64_t>(256) * OVERSEAS_GROWTH_UNIT);
	if (recession) amount = (amount + 1) / 2;
	return static_cast<uint>(std::min<uint64_t>(amount, UINT16_MAX * 16));
}

/**
 * Apply one year of growth.
 * @param growth Current growth multiplier.
 * @param percent Growth per year in percent.
 * @return The new growth multiplier.
 */
uint32_t GrowOverseasProduction(uint32_t growth, uint percent)
{
	uint64_t grown = static_cast<uint64_t>(growth) * (100 + percent) / 100;
	return static_cast<uint32_t>(std::min<uint64_t>(grown, 1000 * OVERSEAS_GROWTH_UNIT));
}

/**
 * Get the most cargo of one type an overseas station holds.
 * @param monthly Monthly production.
 * @param months Stockpile setting in months of production.
 * @return The limit.
 */
uint GetOverseasStockpileLimit(uint monthly, uint months)
{
	return monthly * months;
}

/** Trip time of a ship to the country of an overseas station and back, in days. */
static uint GetOverseasShipTripDays(const Station *st)
{
	uint min_km = UINT_MAX, max_km = 0;
	for (const OverseasCountry &c : _overseas_countries) {
		min_km = std::min<uint>(min_km, c.distance_km);
		max_km = std::max<uint>(max_km, c.distance_km);
	}
	const EconomySettings &eco = _settings_game.economy;
	return GetOverseasTripDaysForDistance(GetOverseasCountry(st->overseas->country).distance_km, min_km, max_km,
			eco.overseas_trip_min_days, eco.overseas_trip_max_days);
}

/**
 * Get the time a vehicle is away when it visits an overseas station.
 * @param st The overseas station.
 * @param type Vehicle type, aircraft are faster than ships.
 * @return The trip time in days.
 */
uint GetOverseasTripDays(const Station *st, VehicleType type)
{
	uint days = GetOverseasShipTripDays(st);
	if (type == VehicleType::Aircraft) days = GetOverseasAircraftTripDays(days, _settings_game.economy.overseas_aircraft_trip_percent);
	return days;
}

/**
 * Get the time a vehicle is away when it visits an overseas station.
 * @param st The overseas station.
 * @param type Vehicle type.
 * @return The trip time in ticks.
 */
uint GetOverseasTripTicks(const Station *st, VehicleType type)
{
	return GetOverseasTripDays(st, type) * TicksPerCalendarDay();
}

/**
 * Get the distance cargo is paid for between an overseas station and its country.
 * @param st The overseas station.
 * @return The distance in tiles.
 */
uint GetOverseasStationVirtualDistance(const Station *st)
{
	return GetOverseasVirtualDistance(GetOverseasCountry(st->overseas->country).distance_km);
}

/**
 * Get the cargo of a kind of product of an overseas station.
 * @param st The overseas station.
 * @param product The kind of product.
 * @return The cargo, INVALID_CARGO if the station does not produce it (for example no mail cargo).
 */
CargoType GetOverseasProductCargo(const Station *st, OverseasProduct product)
{
	switch (product) {
		case OverseasProduct::Passengers: return GetCargoTypeByLabel(CT_PASSENGERS);
		case OverseasProduct::Mail: return GetCargoTypeByLabel(CT_MAIL);
		case OverseasProduct::Cargo: return st->overseas->cargo;
		default: NOT_REACHED();
	}
}

static uint GetOverseasProductBase(OverseasProduct product)
{
	const EconomySettings &eco = _settings_game.economy;
	switch (product) {
		case OverseasProduct::Passengers: return eco.overseas_passengers;
		case OverseasProduct::Mail: return eco.overseas_mail;
		case OverseasProduct::Cargo: return eco.overseas_cargo;
		default: NOT_REACHED();
	}
}

/**
 * Get the current monthly production of a kind of product of an overseas station.
 * @param st The overseas station.
 * @param product The kind of product.
 * @return The amount produced per month.
 */
uint GetOverseasStationMonthlyProduction(const Station *st, OverseasProduct product)
{
	if (!IsValidCargoType(GetOverseasProductCargo(st, product))) return 0;
	return GetOverseasMonthlyProduction(GetOverseasProductBase(product), GetOverseasCountry(st->overseas->country).population,
			st->overseas->growth, EconomyIsInRecession());
}

/**
 * Get the most cargo of a kind of product an overseas station holds.
 * Recessions do not lower the limit.
 * @param st The overseas station.
 * @param product The kind of product.
 * @return The limit.
 */
uint GetOverseasStationStockpileLimit(const Station *st, OverseasProduct product)
{
	if (!IsValidCargoType(GetOverseasProductCargo(st, product))) return 0;
	uint monthly = GetOverseasMonthlyProduction(GetOverseasProductBase(product), GetOverseasCountry(st->overseas->country).population,
			st->overseas->growth, false);
	return GetOverseasStockpileLimit(monthly, _settings_game.economy.overseas_stockpile_months);
}

/**
 * Count the overseas stations a company controls.
 * @param company The company.
 * @return The number of stations.
 */
uint CountOverseasStationsOf(CompanyID company)
{
	uint count = 0;
	for (const Station *st : Station::Iterate()) {
		if (st->overseas != nullptr && st->owner == company) count++;
	}
	return count;
}

/**
 * Get the price a company pays for its next overseas station, with inflation.
 * @param company The company.
 * @return The price.
 */
Money GetOverseasControlPrice(CompanyID company)
{
	Money price = GetOverseasControlBasePrice(static_cast<Money>(_settings_game.economy.overseas_fee), CountOverseasStationsOf(company));
	return price * static_cast<int64_t>(_economy.inflation_prices) >> 16;
}

/**
 * Why can a company not take control of an overseas station?
 * @param st The overseas station.
 * @param company The company.
 * @return The error message, STR_NULL if it can.
 */
StringID GetOverseasControlError(const Station *st, CompanyID company)
{
	if (st->owner == company) return STR_ERROR_OVERSEAS_ALREADY_CONTROLLED;
	if (st->owner != OWNER_NONE) return STR_ERROR_OVERSEAS_CONTROLLED_BY_OTHER;
	if (CountOverseasStationsOf(company) >= _settings_game.economy.overseas_max_per_company) return STR_ERROR_OVERSEAS_TOO_MANY;
	return STR_NULL;
}

/**
 * May vehicles of a company use an overseas station? Only the company that controls it may.
 * @param st The overseas station.
 * @param owner Owner of the vehicle.
 * @return True if they may.
 */
bool CanCompanyUseOverseasStation(const Station *st, Owner owner)
{
	return st->owner == owner && Company::IsValidID(owner);
}

/**
 * Get the extra distance cargo from an overseas country is paid for.
 * @param cp The cargo packet.
 * @return The extra distance in tiles.
 */
uint GetOverseasDistanceBonus(const CargoPacket *cp)
{
	const Station *st = Station::GetIfValid(cp->GetFirstStation());
	if (st == nullptr || st->overseas == nullptr) return 0;
	return GetOverseasStationVirtualDistance(st);
}

/**
 * Get the next hops a vehicle loads cargo for. Cargo distribution does not apply to overseas trips: a vehicle whose
 * next stop is an overseas station loads cargo for any next hop.
 * @param next The next hops of the vehicle.
 * @param list The cargo waiting at the station.
 * @param buffer Storage for the returned hops.
 * @return The hops to load cargo for.
 */
std::span<const StationID> GetOverseasLoadNextHops(std::span<const StationID> next, const StationCargoList &list, std::vector<StationID> &buffer)
{
	const bool overseas = std::ranges::any_of(next, [](StationID id) {
		const Station *st = Station::GetIfValid(id);
		return st != nullptr && st->IsOverseas();
	});
	if (!overseas) return next;

	buffer.assign(next.begin(), next.end());
	for (const auto &entry : static_cast<const StationCargoPacketMap::Map &>(*list.Packets())) {
		if (std::ranges::find(buffer, entry.first) == buffer.end()) buffer.push_back(entry.first);
	}
	return buffer;
}

/**
 * Pick the cargoes an overseas station produces and accepts.
 * It produces a random freight cargo produced by an industry, and accepts an intermediate cargo: one an industry
 * makes from other cargo. Without intermediate cargo it accepts another freight cargo industries accept.
 * @param os The overseas data to fill.
 */
static void ChooseOverseasCargoes(OverseasStation &os)
{
	CargoTypes produced{}, accepted{}, secondary{};
	for (IndustryType it = 0; it < NUM_INDUSTRYTYPES; it++) {
		const IndustrySpec *indsp = GetIndustrySpec(it);
		if (!indsp->enabled) continue;
		CargoTypes inputs{};
		for (CargoType c : indsp->accepts_cargo) {
			if (IsValidCargoType(c)) inputs.Set(c);
		}
		accepted |= inputs;
		for (CargoType c : indsp->produced_cargo) {
			if (!IsValidCargoType(c)) continue;
			produced.Set(c);
			/* Made from other cargo; a bank turning valuables into valuables is not processing. */
			CargoTypes other_inputs = inputs;
			other_inputs.Reset(c);
			if (other_inputs.Any()) secondary.Set(c);
		}
	}

	auto is_freight = [](CargoType c) {
		return CargoSpec::Get(c)->IsValid() && !IsCargoInClass(c, CargoClass::Passengers) && !IsCargoInClass(c, CargoClass::Mail);
	};
	auto pick = [&](CargoTypes mask) -> CargoType {
		std::vector<CargoType> candidates;
		for (CargoType c : mask) {
			if (is_freight(c)) candidates.push_back(c);
		}
		if (candidates.empty()) return INVALID_CARGO;
		return candidates[RandomRange(static_cast<uint32_t>(candidates.size()))];
	};

	os.cargo = pick(produced);

	CargoTypes intermediate = secondary;
	if (IsValidCargoType(os.cargo)) {
		intermediate.Reset(os.cargo);
		accepted.Reset(os.cargo);
	}
	os.accepted_cargo = pick(intermediate);
	if (!IsValidCargoType(os.accepted_cargo)) os.accepted_cargo = pick(accepted);
}

/**
 * Create an overseas station.
 * @param tile Sea tile at the map border.
 * @param country The country.
 */
static void CreateOverseasStation(TileIndex tile, uint8_t country)
{
	Station *st = Station::Create(tile);
	_station_kdtree.Insert(st->index);
	st->town = ClosestTownFromTile(tile, UINT_MAX);
	st->string_id = STR_SV_STNAME_OVERSEAS;

	st->overseas = std::make_unique<OverseasStation>();
	st->overseas->country = country;
	ChooseOverseasCargoes(*st->overseas);

	MakeOverseasStation(tile, st->index, GetWaterClass(tile));

	st->owner = OWNER_NONE;
	st->airport.type = AT_OILRIG;
	st->airport.rotation = Direction::N;
	st->airport.Add(tile);
	st->ship_station.Add(tile);
	st->facilities = {StationFacility::Airport, StationFacility::Dock};
	st->build_date = CalTime::CurDate();
	UpdateStationDockingTiles(st);

	st->rect.BeforeAddTile(tile, StationRect::ADD_FORCE);
	st->UpdateVirtCoord();
	st->RecomputeCatchment();
	UpdateStationAcceptance(st, false);
}

/** Is this tile a place for an overseas station: open sea at the map border with sea next to it for docking? */
static bool IsOverseasStationLocation(TileIndex tile)
{
	if (!IsTileType(tile, TileType::Water) || !IsWater(tile) || !IsTileFlat(tile) || GetWaterClass(tile) != WaterClass::Sea) return false;
	if (IsDockingTile(tile)) return false;
	bool dock = false;
	for (DiagDirection dir = DiagDirection::Begin; dir < DiagDirection::End; dir++) {
		TileIndex t = TileAddByDiagDir(tile, dir);
		if (!IsValidTile(t)) continue;
		/* Keep away from other water stations and industries, so docking tiles stay unambiguous. */
		if (IsTileType(t, TileType::Station) || IsTileType(t, TileType::Industry)) return false;
		if (IsTileType(t, TileType::Water) && IsWater(t) && GetWaterClass(t) == WaterClass::Sea && !IsDockingTile(t)) dock = true;
	}
	return dock;
}

/** Create the overseas stations of a new game. Nothing happens if the map already has overseas stations. */
void GenerateOverseasStations()
{
	const uint want = std::min<uint>(_settings_game.economy.overseas_stations, NUM_OVERSEAS_COUNTRIES);
	if (want == 0 || Town::GetNumItems() == 0) return;
	for (const Station *st : Station::Iterate()) {
		if (st->overseas != nullptr) return;
	}

	std::vector<TileIndex> candidates;
	for (TileIndex tile(0); tile < Map::Size(); ++tile) {
		bool border = false;
		for (DiagDirection dir = DiagDirection::Begin; dir < DiagDirection::End; dir++) {
			if (IsValidTile(tile) && DistanceFromEdgeDir(tile, dir) == 0) border = true;
		}
		if (border && IsOverseasStationLocation(tile)) candidates.push_back(tile);
	}
	if (candidates.empty()) return;

	/* Shuffle, then take the candidates that are far enough from the ones taken already; shrink the spacing until there are enough. */
	for (size_t i = candidates.size() - 1; i > 0; i--) std::swap(candidates[i], candidates[RandomRange(static_cast<uint32_t>(i + 1))]);

	std::vector<TileIndex> chosen;
	const uint perimeter = 2 * (Map::SizeX() + Map::SizeY());
	for (uint spacing = std::max<uint>(8, perimeter / (want * 2)); chosen.size() < want; spacing /= 2) {
		for (TileIndex tile : candidates) {
			if (chosen.size() >= want) break;
			bool near = std::ranges::any_of(chosen, [&](TileIndex t) { return DistanceManhattan(t, tile) < std::max<uint>(spacing, 3); });
			if (!near) chosen.push_back(tile);
		}
		if (spacing <= 3) break;
	}

	std::array<uint8_t, NUM_OVERSEAS_COUNTRIES> countries;
	for (uint i = 0; i < NUM_OVERSEAS_COUNTRIES; i++) countries[i] = static_cast<uint8_t>(i);
	for (uint i = NUM_OVERSEAS_COUNTRIES - 1; i > 0; i--) std::swap(countries[i], countries[RandomRange(i + 1)]);

	for (size_t i = 0; i < chosen.size(); i++) {
		if (!Station::CanAllocateItem()) break;
		CreateOverseasStation(chosen[i], countries[i]);
	}
}

/**
 * Add cargo to the stockpile of an overseas station, up to its limit.
 * @param st The overseas station.
 * @param cargo The cargo.
 * @param amount Amount to add.
 * @param limit Most cargo the station holds.
 */
static void AddOverseasCargo(Station *st, CargoType cargo, uint amount, uint limit)
{
	GoodsEntry &ge = st->goods[cargo];
	const uint waiting = ge.CargoTotalCount();
	if (waiting >= limit) return;
	amount = std::min(amount, limit - waiting);
	while (amount > 0 && CargoPacket::CanAllocateItem()) {
		const uint count = std::min<uint>(amount, CargoPacket::MAX_COUNT);
		ge.CreateData().cargo.Append(CargoPacket::Create(st->index, static_cast<uint16_t>(count), Source{Source::Invalid, SourceType::Industry}), StationID::Invalid());
		amount -= count;
	}
	if (!ge.HasRating()) {
		ge.status.Set(GoodsEntry::State::Rating);
		ge.rating = 255;
	}
	SetWindowDirty(WindowClass::StationView, st->index);
}

/** Produce the cargo of all overseas stations, every day. */
void OverseasStationsDailyLoop()
{
	for (Station *st : Station::Iterate()) {
		if (st->overseas == nullptr) continue;
		for (uint p = 0; p < NUM_OVERSEAS_PRODUCTS; p++) {
			const OverseasProduct product = static_cast<OverseasProduct>(p);
			const CargoType cargo = GetOverseasProductCargo(st, product);
			if (!IsValidCargoType(cargo)) continue;

			/* Twelve months in a year, spread over the days. */
			uint32_t &fract = st->overseas->fract[p];
			fract += GetOverseasStationMonthlyProduction(st, product) * 12;
			const uint amount = fract / DAYS_IN_YEAR;
			fract %= DAYS_IN_YEAR;
			if (amount > 0) AddOverseasCargo(st, cargo, amount, GetOverseasStationStockpileLimit(st, product));
		}
	}
}

/** The world gets bigger: raise the production of all overseas stations, every year. */
void OverseasStationsYearlyLoop()
{
	for (Station *st : Station::Iterate()) {
		if (st->overseas == nullptr) continue;
		st->overseas->growth = GrowOverseasProduction(st->overseas->growth, _settings_game.economy.overseas_growth);
		SetWindowDirty(WindowClass::StationView, st->index);
	}
}

/**
 * Set the acceptance of an overseas station: passengers, mail and its accepted cargo, in any amount.
 * @param st The overseas station.
 */
void UpdateOverseasAcceptance(Station *st)
{
	CargoTypes accepts{};
	for (CargoType c : {GetCargoTypeByLabel(CT_PASSENGERS), GetCargoTypeByLabel(CT_MAIL), st->overseas->accepted_cargo}) {
		if (IsValidCargoType(c)) accepts.Set(c);
	}
	st->always_accepted = accepts;
	for (CargoType c : EnumRange(NUM_CARGO)) {
		st->goods[c].status.Set(GoodsEntry::State::Acceptance, accepts.Test(c));
	}
}

/** Check the overseas stations after loading a game: cargoes removed by NewGRF changes are picked again. */
void AfterLoadOverseasStations()
{
	for (Station *st : Station::Iterate()) {
		if (st->overseas == nullptr) continue;
		OverseasStation &os = *st->overseas;
		if (os.country >= NUM_OVERSEAS_COUNTRIES) os.country = 0;
		const bool cargo_ok = IsValidCargoType(os.cargo) && CargoSpec::Get(os.cargo)->IsValid();
		const bool accepted_ok = IsValidCargoType(os.accepted_cargo) && CargoSpec::Get(os.accepted_cargo)->IsValid();
		if (!cargo_ok || !accepted_ok) {
			SavedRandomSeeds saved;
			SaveRandomSeeds(&saved);
			SetRandomSeed(st->index.base() * 7919 + os.country);
			OverseasStation picked;
			ChooseOverseasCargoes(picked);
			RestoreRandomSeeds(saved);
			if (!cargo_ok) os.cargo = picked.cargo;
			if (!accepted_ok) os.accepted_cargo = picked.accepted_cargo;
		}
		UpdateOverseasAcceptance(st);
	}
}

/** Show or hide all parts of a vehicle. */
static void SetOverseasVehicleHidden(Vehicle *v, bool hidden)
{
	v->MarkAllViewportsDirty();
	for (Vehicle *u = v; u != nullptr; u = u->Next()) {
		u->vehstatus.Set(VehState::Hidden, hidden);
		u->UpdateIsDrawn();
	}
	if (v->type == VehicleType::Aircraft) {
		Aircraft *a = Aircraft::From(v);
		SetAircraftPosition(a, a->x_pos, a->y_pos, a->z_pos);
	} else {
		for (Vehicle *u = v; u != nullptr; u = u->Next()) u->UpdatePositionAndViewport();
	}
	v->MarkAllViewportsDirty();
}

/**
 * Deliver the cargo of a vehicle leaving for an overseas country.
 * Cargo the station accepts is paid for as if it travelled the virtual distance and half of the trip time on top.
 * Cargo that came from this station goes back to its stockpile without payment, other cargo stays aboard.
 * @param v The vehicle.
 * @param st The overseas station.
 * @param extra_periods Cargo aging periods of the trip to the country.
 */
static void DeliverOverseasCargo(Vehicle *v, Station *st, uint extra_periods)
{
	if (!CargoPayment::CanAllocateItem()) return;
	CargoPayment *payment = CargoPayment::Create(v);
	v->cargo_payment = payment;
	payment->overseas_extra_distance = GetOverseasStationVirtualDistance(st);
	payment->overseas_extra_periods = extra_periods;

	const bool goto_here = v->current_order.IsType(OT_GOTO_STATION) && v->current_order.GetDestination() == st->index;
	for (Vehicle *u = v; u != nullptr; u = u->Next()) {
		if (u->cargo.StoredCount() == 0) continue;
		const CargoType cargo = u->cargo_type;
		if (!st->goods[cargo].status.Test(GoodsEntry::State::Acceptance)) continue;
		if (goto_here) {
			OrderUnloadType unload = v->current_order.GetCargoUnloadType(cargo);
			if (unload == OrderUnloadType::NoUnload || unload == OrderUnloadType::Transfer) continue;
		}

		uint returned = 0;
		const TileIndex tile = u->GetCargoTile();
		for (CargoPacket *cp : *u->cargo.Packets()) {
			if (cp->GetFirstStation() == st->index) {
				returned += cp->Count();
			} else {
				payment->PayFinalDelivery(cargo, cp, cp->Count(), tile);
			}
		}
		u->cargo.Truncate();
		if (returned > 0) AddOverseasCargo(st, cargo, returned, UINT_MAX);
	}
	delete payment;
}

/**
 * Load cargo from the stockpile of an overseas station into a vehicle coming back.
 * The cargo is aged by half of the trip.
 * @param v The vehicle.
 * @param st The overseas station.
 * @param periods Cargo aging periods of the trip back.
 */
static void LoadOverseasCargo(Vehicle *v, Station *st, uint periods)
{
	const bool goto_here = v->current_order.IsType(OT_GOTO_STATION) && v->current_order.GetDestination() == st->index;
	for (Vehicle *u = v; u != nullptr; u = u->Next()) {
		const CargoType cargo = u->cargo_type;
		if (u->cargo_cap == 0 || !IsValidCargoType(cargo)) continue;
		if (goto_here && v->current_order.GetCargoLoadType(cargo) == OrderLoadType::NoLoad) continue;
		if (u->cargo.StoredCount() >= u->cargo_cap) continue;

		GoodsEntry &ge = st->goods[cargo];
		if (ge.data == nullptr) continue;
		uint amount = std::min<uint>(u->cargo_cap - u->cargo.StoredCount(), ge.data->cargo.AvailableCount());
		if (amount == 0) continue;
		amount = ge.data->cargo.Truncate(amount);

		const TileIndex tile = u->GetCargoTile();
		while (amount > 0 && CargoPacket::CanAllocateItem()) {
			const uint count = std::min<uint>(amount, CargoPacket::MAX_COUNT);
			CargoPacket *cp = CargoPacket::Create(static_cast<uint16_t>(count), static_cast<uint16_t>(std::min<uint>(periods, UINT16_MAX)), st->index, INVALID_TILE, 0);
			cp->UpdateLoadingTile(tile);
			u->cargo.Append(cp, VehicleCargoList::MoveToAction::Keep);
			amount -= count;
		}
		ge.time_since_pickup = 0;
	}
	v->MarkDirty();
	SetWindowDirty(WindowClass::StationView, st->index);
}

/**
 * A ship or aircraft reached an overseas station: it delivers its cargo and leaves the map for the trip to the country.
 * @param v The vehicle.
 * @param st The overseas station.
 * @return False if the vehicle may not use the station, it then skips the order.
 */
bool OverseasVehicleDepart(Vehicle *v, Station *st)
{
	if (!CanCompanyUseOverseasStation(st, v->owner)) return false;

	/* Road vehicles do not go overseas. */
	if (IsCarFerry(v) && !Ship::From(v)->ferry_vehicles.empty()) {
		if (v->owner == _local_company) {
			AddVehicleAdviceNewsItem(AdviceType::Order, GetEncodedString(STR_NEWS_OVERSEAS_CAR_FERRY_LOADED, v->index, st->index), v->index);
		}
		return false;
	}

	const uint trip_ticks = GetOverseasTripTicks(st, v->type);
	const uint period = std::max<uint>(1, v->vcache.cached_cargo_age_period);

	v->last_station_visited = st->index;
	for (Vehicle *u = v; u != nullptr; u = u->Next()) u->last_loading_station = StationID::Invalid();
	v->vehicle_flags.Reset(VehicleFlag::LastLoadStationSeparate);

	DeliverOverseasCargo(v, st, trip_ticks / 2 / period);

	v->overseas_station = st->index;
	v->overseas_trip_ticks = trip_ticks;
	v->overseas_return = _state_ticks + trip_ticks;
	if (v->type == VehicleType::Ship) {
		v->cur_speed = 0;
		v->subspeed = 0;
		Ship::From(v)->cached_path.clear();
	}
	SetOverseasVehicleHidden(v, true);

	SetWindowDirty(WindowClass::VehicleView, v->index);
	SetWindowDirty(WindowClass::VehicleDetails, v->index);
	DirtyVehicleListWindowForVehicle(v);
	SetWindowDirty(WindowClass::StationView, st->index);
	return true;
}

/**
 * Get the number of days until a vehicle comes back from overseas.
 * @param v The vehicle.
 * @return Days left, rounded up.
 */
uint GetOverseasDaysLeft(const Vehicle *v)
{
	if (!v->IsOverseas() || _state_ticks >= v->First()->overseas_return) return 0;
	const int64_t ticks = (v->First()->overseas_return - _state_ticks).base();
	return static_cast<uint>(CeilDiv(static_cast<uint64_t>(ticks), static_cast<uint64_t>(std::max<Ticks>(1, TicksPerCalendarDay()))));
}

/**
 * Tick of a vehicle that is away overseas: it comes back when the trip is over, with cargo of the country.
 * @param v The vehicle.
 */
void OverseasVehicleTick(Vehicle *v)
{
	if (_state_ticks < v->overseas_return) return;

	Station *st = Station::GetIfValid(v->overseas_station);
	const uint period = std::max<uint>(1, v->vcache.cached_cargo_age_period);
	if (st != nullptr && st->overseas != nullptr) LoadOverseasCargo(v, st, v->overseas_trip_ticks / 2 / period);

	const bool goto_here = v->current_order.IsType(OT_GOTO_STATION) && v->current_order.GetDestination() == v->overseas_station;
	v->overseas_station = StationID::Invalid();
	v->overseas_return = StateTicks{};
	v->overseas_trip_ticks = 0;

	/* The vehicle was serviced abroad. */
	v->date_of_last_service = EconTime::CurDate();
	v->date_of_last_service_newgrf = CalTime::CurDate();

	SetOverseasVehicleHidden(v, false);

	if (goto_here) {
		v->current_order.MakeLeaveStation();
		v->IncrementRealOrderIndex();
	}

	SetWindowDirty(WindowClass::VehicleView, v->index);
	SetWindowDirty(WindowClass::VehicleDetails, v->index);
	DirtyVehicleListWindowForVehicle(v);
}

/**
 * Take or give up control of an overseas station.
 * @param flags Type of operation.
 * @param station The overseas station.
 * @param take True to take control, false to give it up.
 * @return The cost of this operation or an error.
 */
CommandCost CmdOverseasStationControl(DoCommandFlags flags, StationID station, bool take)
{
	if (_game_mode != GameMode::Normal || !Company::IsValidID(_current_company)) return CMD_ERROR;
	Station *st = Station::GetIfValid(station);
	if (st == nullptr || st->overseas == nullptr) return CMD_ERROR;

	if (!take) {
		if (st->owner != _current_company) return CMD_ERROR;
		if (flags.Test(DoCommandFlag::Execute)) {
			st->owner = OWNER_NONE;
			st->UpdateVirtCoord();
			InvalidateWindowData(WindowClass::StationList, _current_company);
			InvalidateWindowData(WindowClass::StationView, st->index);
			MarkWholeScreenDirty();
		}
		return CommandCost();
	}

	StringID error = GetOverseasControlError(st, _current_company);
	if (error != STR_NULL) return CommandCost(error);

	CommandCost cost(ExpensesType::Construction, GetOverseasControlPrice(_current_company));
	if (flags.Test(DoCommandFlag::Execute)) {
		st->owner = _current_company;
		st->UpdateVirtCoord();
		InvalidateWindowData(WindowClass::StationList, _current_company);
		InvalidateWindowData(WindowClass::StationView, st->index);
		MarkWholeScreenDirty();

		/* Rivals get to know. */
		if (_local_company != _current_company) {
			auto cni = std::make_unique<CompanyNewsInformation>(STR_NEWS_OVERSEAS_CONTROL_TITLE, Company::Get(_current_company));
			EncodedString headline = GetEncodedString(STR_NEWS_OVERSEAS_CONTROL, cni->company_name, st->index);
			AddNewsItem(std::move(headline), NewsType::CompanyInfo, NewsStyle::Company, {}, st->index, {}, std::move(cni));
		}
	}
	return cost;
}
