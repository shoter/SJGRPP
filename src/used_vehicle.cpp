/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle.cpp Used vehicle market: listings of second-hand vehicles and vehicle owner history. */

#include "stdafx.h"
#include "used_vehicle.h"
#include "company_base.h"
#include "core/random_func.hpp"
#include "date_func.h"
#include "debug_bridge.h"
#include "engine_base.h"
#include "openttd.h"
#include "rail.h"
#include "road.h"
#include "settings_type.h"
#include "strings_func.h"
#include "train.h"
#include "vehicle_base.h"
#include "window_func.h"

#include <map>

#include "safeguards.h"

UsedVehicleMarket _used_vehicle_market;
std::map<VehicleID, VehicleOwnerInfo> _vehicle_owner_info;

/** Listings as "id:engine:age:seller:previous owners" separated by ';', for the debug bridge. */
static std::string DumpUsedVehicleListings()
{
	format_buffer buf;
	for (const UsedVehicleListing &l : _used_vehicle_market.listings) {
		buf.format("{}:{}:{}:{}:{};", l.id, l.engine, l.age, l.seller, l.history.size());
	}
	return buf.to_string();
}

DEBUG_BRIDGE_WATCH("used_vehicles.count", _used_vehicle_market.listings.size());
DEBUG_BRIDGE_WATCH("used_vehicles.listings", DumpUsedVehicleListings());

static constexpr int32_t USED_VEHICLE_SLOW_DEPRECIATION_DAYS = 3 * DAYS_IN_YEAR; ///< Period in which the price drops only slightly.

std::string VehicleOwnerRecord::GetCompanyName() const
{
	if (!this->custom_name.empty()) return this->custom_name;
	if (this->name_1 == STR_NULL) return {};
	return GetString(this->name_1, this->name_2);
}

/**
 * Get the price of a used vehicle.
 * During the first 3 years the price drops linearly from 100% to 95% of the price of a new vehicle,
 * after that quadratically to 25% at the end of its lifespan.
 * @return The price.
 */
Money UsedVehicleListing::GetPrice() const
{
	const Engine *e = Engine::Get(this->engine);
	const int64_t life = std::max<int64_t>(1, e->GetLifeLengthInDays().base());
	const int64_t age = std::clamp<int64_t>(this->age.base(), 0, life);

	int64_t permyriad;
	if (life <= USED_VEHICLE_SLOW_DEPRECIATION_DAYS) {
		permyriad = 10000 - (7500 * age) / life;
	} else if (age <= USED_VEHICLE_SLOW_DEPRECIATION_DAYS) {
		permyriad = 10000 - (500 * age) / USED_VEHICLE_SLOW_DEPRECIATION_DAYS;
	} else {
		const int64_t num = age - USED_VEHICLE_SLOW_DEPRECIATION_DAYS;
		const int64_t den = life - USED_VEHICLE_SLOW_DEPRECIATION_DAYS;
		permyriad = 9500 - (7000 * num * num) / (den * den);
	}
	return e->GetCost() * permyriad / 10000;
}

/**
 * Get the reliability a used vehicle starts with: the current reliability of the engine,
 * lowered by up to 25% as the vehicle approaches the end of its lifespan.
 * @return The reliability.
 */
uint16_t UsedVehicleListing::GetReliability() const
{
	const Engine *e = Engine::Get(this->engine);
	const int64_t life = std::max<int64_t>(1, e->GetLifeLengthInDays().base());
	const int64_t age = std::clamp<int64_t>(this->age.base(), 0, life);
	return static_cast<uint16_t>(e->reliability - (e->reliability * age) / (4 * life));
}

bool IsUsedVehicleMarketEnabled()
{
	return _settings_game.vehicle.used_vehicle_market;
}

/**
 * Check whether vehicles of an engine type can be traded on the used vehicle market.
 * Train wagons are excluded, only locomotives are traded.
 * @param engine Engine type.
 * @return True if the engine type can be traded.
 */
bool IsUsedVehicleMarketEngine(EngineID engine)
{
	const Engine *e = Engine::GetIfValid(engine);
	if (e == nullptr || !e->IsEnabled()) return false;
	switch (e->type) {
		case VehicleType::Train: return e->VehInfo<RailVehicleInfo>().railveh_type != RailVehicleType::Wagon;
		case VehicleType::Road:
		case VehicleType::Ship:
		case VehicleType::Aircraft:
			return true;
		default:
			return false;
	}
}

/**
 * Check whether a company can buy a used vehicle of an engine type.
 * Unlike #IsEngineBuildable this does not require the engine to be on sale as a new vehicle.
 * @param engine Engine type.
 * @param type Vehicle type of the depot.
 * @param company Company buying the vehicle.
 * @return True if the vehicle can be bought.
 */
bool IsUsedEngineBuildable(EngineID engine, VehicleType type, CompanyID company)
{
	if (!IsUsedVehicleMarketEngine(engine)) return false;
	const Engine *e = Engine::Get(engine);
	if (e->type != type) return false;

	const Company *c = Company::GetIfValid(company);
	if (c == nullptr) return false;
	if (type == VehicleType::Train && !GetAllCompatibleRailTypes(e->VehInfo<RailVehicleInfo>().railtypes).Any(c->avail_railtypes)) return false;
	if (type == VehicleType::Road && !GetRoadTypeInfo(e->VehInfo<RoadVehicleInfo>().roadtype)->powered_roadtypes.Any(c->avail_roadtypes)) return false;
	return true;
}

UsedVehicleListing *GetUsedVehicleListing(uint32_t id)
{
	for (UsedVehicleListing &l : _used_vehicle_market.listings) {
		if (l.id == id) return &l;
	}
	return nullptr;
}

void RemoveUsedVehicleListing(uint32_t id)
{
	auto &listings = _used_vehicle_market.listings;
	listings.erase(std::remove_if(listings.begin(), listings.end(), [id](const UsedVehicleListing &l) { return l.id == id; }), listings.end());
	InvalidateUsedVehicleWindows();
}

void InitializeUsedVehicleMarket()
{
	_used_vehicle_market = {};
	_vehicle_owner_info.clear();
}

/** Whether new listings of an engine type are generated: the engine type must be on sale as a new vehicle. */
static bool CanGenerateUsedVehicleListings(const Engine *e)
{
	return IsUsedVehicleMarketEngine(e->index) && e->flags.Test(EngineFlag::Available) && e->company_avail.Any();
}

/**
 * Generate a listing with a random age.
 * The vehicle cannot be older than its lifespan, nor built before the engine was introduced.
 */
static void GenerateUsedVehicleListing(const Engine *e)
{
	const int32_t max_age = std::min<int32_t>(e->GetLifeLengthInDays().base(), (CalTime::CurDate() - e->intro_date).base());

	UsedVehicleListing &l = _used_vehicle_market.listings.emplace_back();
	l.id = _used_vehicle_market.next_id++;
	l.engine = e->index;
	l.age = CalTime::DateDelta{max_age > 0 ? static_cast<int32_t>(RandomRange(static_cast<uint32_t>(max_age) + 1)) : 0};
}

/**
 * Get the maximum age in whole years of a generated used vehicle: it can't be older than its lifespan, nor built before the engine was introduced.
 * @param life_length Lifespan of the engine.
 * @param since_intro Time since the engine was introduced.
 * @return The maximum age in years, 0 when the engine is less than a year old.
 */
int GetUsedVehicleMaxAgeYears(CalTime::DateDelta life_length, CalTime::DateDelta since_intro)
{
	return std::max<int32_t>(0, std::min(life_length.base(), since_intro.base()) / DAYS_IN_YEAR);
}

/** Get the setting with the maximum number of used vehicles of a vehicle type at the start of the game. */
static uint GetUsedVehicleInitialMax(VehicleType type)
{
	switch (type) {
		case VehicleType::Train: return _settings_game.vehicle.used_vehicle_initial_max_train;
		case VehicleType::Road: return _settings_game.vehicle.used_vehicle_initial_max_road;
		case VehicleType::Ship: return _settings_game.vehicle.used_vehicle_initial_max_ship;
		case VehicleType::Aircraft: return _settings_game.vehicle.used_vehicle_initial_max_aircraft;
		default: NOT_REACHED();
	}
}

/**
 * Fill the market: for each vehicle type add between 0 and the setting's maximum used vehicles,
 * each a random engine type on sale aged a random number of whole years.
 */
static void SeedUsedVehicleMarket()
{
	for (VehicleType type : { VehicleType::Train, VehicleType::Road, VehicleType::Ship, VehicleType::Aircraft }) {
		std::vector<std::pair<const Engine *, int>> candidates;
		for (const Engine *e : Engine::IterateType(type)) {
			if (!CanGenerateUsedVehicleListings(e)) continue;
			const int max_years = GetUsedVehicleMaxAgeYears(e->GetLifeLengthInDays(), CalTime::CurDate() - e->intro_date);
			if (max_years > 0) candidates.emplace_back(e, max_years);
		}
		if (candidates.empty()) continue;

		const uint count = RandomRange(GetUsedVehicleInitialMax(type) + 1);
		for (uint i = 0; i < count; i++) {
			const auto &[e, max_years] = candidates[RandomRange(static_cast<uint32_t>(candidates.size()))];
			UsedVehicleListing &l = _used_vehicle_market.listings.emplace_back();
			l.id = _used_vehicle_market.next_id++;
			l.engine = e->index;
			l.age = CalTime::DateDelta{static_cast<int32_t>(1 + RandomRange(static_cast<uint32_t>(max_years))) * DAYS_IN_YEAR};
		}
	}
	_used_vehicle_market.seeded = true;
}

/** Fill the market when it is enabled (start of the game, or the setting was switched on) and empty it when it is disabled. */
void UsedVehicleMarketDailyLoop()
{
	if (_game_mode != GameMode::Normal) return;

	if (!IsUsedVehicleMarketEnabled()) {
		if (_used_vehicle_market.seeded || !_used_vehicle_market.listings.empty()) {
			_used_vehicle_market.seeded = false;
			_used_vehicle_market.listings.clear();
			InvalidateUsedVehicleWindows();
		}
		return;
	}

	if (!_used_vehicle_market.seeded) {
		SeedUsedVehicleMarket();
		InvalidateUsedVehicleWindows();
	}
}

/** Age all listings by a year, remove the ones past their lifespan and add new ones. */
void UsedVehicleMarketYearlyLoop()
{
	if (_game_mode != GameMode::Normal || !IsUsedVehicleMarketEnabled() || !_used_vehicle_market.seeded) return;

	auto &listings = _used_vehicle_market.listings;
	for (UsedVehicleListing &l : listings) l.age += CalTime::DateDelta{DAYS_IN_YEAR};
	listings.erase(std::remove_if(listings.begin(), listings.end(), [](const UsedVehicleListing &l) {
		const Engine *e = Engine::GetIfValid(l.engine);
		return e == nullptr || l.age > e->GetLifeLengthInDays();
	}), listings.end());

	const uint chance = _settings_game.vehicle.used_vehicle_yearly_chance;
	for (const Engine *e : Engine::Iterate()) {
		if (!CanGenerateUsedVehicleListings(e)) continue;
		if (RandomRange(100) < chance) GenerateUsedVehicleListing(e);
	}

	InvalidateUsedVehicleWindows();
}

const VehicleOwnerInfo *GetVehicleOwnerInfo(VehicleID veh)
{
	auto it = _vehicle_owner_info.find(veh);
	return it != _vehicle_owner_info.end() ? &it->second : nullptr;
}

VehicleOwnerInfo &GetOrCreateVehicleOwnerInfo(VehicleID veh)
{
	return _vehicle_owner_info[veh];
}

void RemoveVehicleOwnerInfo(VehicleID veh)
{
	_vehicle_owner_info.erase(veh);
}

/**
 * Get the date the current owner acquired a vehicle.
 * @param v Vehicle.
 * @return The date it was bought used, or the date it was built.
 */
CalTime::Date GetVehicleAcquiredDate(const Vehicle *v)
{
	const VehicleOwnerInfo *info = GetVehicleOwnerInfo(v->index);
	if (info != nullptr) return info->acquired;
	return CalTime::CurDate() - v->age;
}

VehicleOwnerRecord MakeVehicleOwnerRecord(CompanyID company, CalTime::Date start, CalTime::Date end)
{
	VehicleOwnerRecord record;
	record.company = company;
	record.start = start;
	record.end = end;
	if (const Company *c = Company::GetIfValid(company); c != nullptr) {
		record.custom_name = c->name;
		record.name_1 = c->name_1;
		record.name_2 = c->name_2;
	}
	return record;
}

/**
 * Record that a vehicle changes owner without being sold, e.g. when its company is bought by another one.
 * @param v Vehicle, still with its old owner.
 * @param new_owner The new owner.
 */
void RecordVehicleOwnerChange(Vehicle *v, CompanyID new_owner)
{
	if (v->owner == new_owner || !Company::IsValidID(new_owner)) return;
	if (v->type == VehicleType::Train ? !Train::From(v)->IsEngine() : !v->IsPrimaryVehicle()) return;

	const CalTime::Date acquired = GetVehicleAcquiredDate(v);
	VehicleOwnerInfo &info = GetOrCreateVehicleOwnerInfo(v->index);
	info.history.push_back(MakeVehicleOwnerRecord(v->owner, acquired, CalTime::CurDate()));
	info.acquired = CalTime::CurDate();
}

/**
 * Collect the vehicles that go to the used vehicle market when a vehicle is sold.
 * Must be called before the vehicle is deleted.
 * @param v Vehicle being sold.
 * @param sell_chain Whether the vehicles behind it in a train are sold as well.
 * @param[out] out The collected vehicles.
 */
void CollectUsedVehicleSaleCandidates(const Vehicle *v, bool sell_chain, std::vector<UsedVehicleSaleCandidate> &out)
{
	auto add = [&](const Vehicle *u, std::string_view name) {
		if (!IsUsedVehicleMarketEngine(u->engine_type)) return;
		if (u->age > u->max_age) return;

		UsedVehicleSaleCandidate &c = out.emplace_back();
		c.engine = u->engine_type;
		c.age = u->age;
		c.seller = u->owner;
		c.name = name;
		if (const VehicleOwnerInfo *info = GetVehicleOwnerInfo(u->index); info != nullptr) c.history = info->history;
		c.history.push_back(MakeVehicleOwnerRecord(u->owner, GetVehicleAcquiredDate(u), CalTime::CurDate()));
	};

	if (v->type == VehicleType::Train) {
		for (const Train *u = Train::From(v); u != nullptr; u = sell_chain ? u->GetNextUnit() : nullptr) {
			if (u->IsEngine()) add(u, u->name);
		}
	} else {
		add(v->First(), v->First()->name);
	}
}

void AddUsedVehicleListings(std::vector<UsedVehicleSaleCandidate> &&candidates)
{
	if (candidates.empty()) return;
	for (UsedVehicleSaleCandidate &c : candidates) {
		UsedVehicleListing &l = _used_vehicle_market.listings.emplace_back();
		l.id = _used_vehicle_market.next_id++;
		l.engine = c.engine;
		l.age = c.age;
		l.seller = c.seller;
		l.name = std::move(c.name);
		l.history = std::move(c.history);
	}
	InvalidateUsedVehicleWindows();
}

void InvalidateUsedVehicleWindows()
{
	InvalidateWindowClassesData(WindowClass::BuyUsedVehicle);
}
