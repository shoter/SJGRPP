/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file car_ferry.cpp Road vehicles carried by ships (car ferries). */

#include "stdafx.h"
#include "car_ferry.h"
#include "order_base.h"
#include "roadstop_base.h"
#include "roadveh.h"
#include "ship.h"
#include "station_base.h"
#include "vehicle_func.h"
#include "window_func.h"

#include "safeguards.h"

void RoadVehBoardFerry(RoadVehicle *v, VehicleID ferry);
bool RoadVehCanLeaveFerryOnto(const RoadVehicle *v, TileIndex tile, DiagDirection dir);
void RoadVehLeaveFerry(RoadVehicle *v, TileIndex tile, DiagDirection dir);

/** Ticks between two road vehicles driving on or off a car ferry. */
static constexpr uint16_t FERRY_TRANSFER_TICKS = 20;

/** Road vehicles that will board a car ferry at the end of the current station loading step: (ship, road vehicle). */
static std::vector<std::pair<VehicleID, VehicleID>> _pending_ferry_boardings;

std::optional<size_t> FindFerryLeaveOrder(std::span<const FerryOrderStop> orders, size_t board_index)
{
	const size_t n = orders.size();
	for (size_t i = 1; i < n; i++) {
		const size_t idx = (board_index + i) % n;
		if (orders[idx].station != StationID::Invalid() && orders[idx].action == FerryAction::Leave) return idx;
	}
	return std::nullopt;
}

bool FerryRouteCallsAt(std::span<const FerryOrderStop> orders, StationID station)
{
	if (station == StationID::Invalid()) return false;
	return std::ranges::any_of(orders, [&](const FerryOrderStop &o) { return o.station == station; });
}

std::optional<std::vector<uint>> GetFerrySpawnFrames(std::span<const uint> lengths, uint max_frame)
{
	if (lengths.empty()) return std::nullopt;

	/* The last part starts at the tile edge, every other part is one part length ahead of the next one. */
	uint front = 0;
	for (size_t i = 0; i + 1 < lengths.size(); i++) front += lengths[i];
	if (front > max_frame) return std::nullopt;

	std::vector<uint> frames;
	frames.reserve(lengths.size());
	uint frame = front;
	for (size_t i = 0; i < lengths.size(); i++) {
		frames.push_back(frame);
		if (i + 1 < lengths.size()) frame -= lengths[i];
	}
	return frames;
}

bool IsCarFerry(const Vehicle *v)
{
	return v != nullptr && v->type == VehicleType::Ship && Ship::From(v)->flags.Test(VehicleShipFlag::CarFerry);
}

/**
 * How many road vehicles a car ferry carries.
 * @param v The ship.
 * @return Its capacity in road vehicles, which is its cargo capacity.
 */
uint GetCarFerryCapacity(const Vehicle *v)
{
	return v->cargo_cap;
}

bool IsRoadVehicleAboardFerry(const Vehicle *v)
{
	return v->type == VehicleType::Road && RoadVehicle::From(v)->IsAboardFerry();
}

static std::vector<FerryOrderStop> GetFerryOrderStops(const Vehicle *v)
{
	std::vector<FerryOrderStop> stops;
	for (const Order *o : v->Orders()) {
		if (o->IsType(OT_GOTO_STATION)) {
			stops.push_back({o->GetDestination().ToStationID(), o->GetFerryAction()});
		} else {
			stops.push_back({StationID::Invalid(), FerryAction::None});
		}
	}
	return stops;
}

/**
 * Get the car ferry action of the order a road vehicle is loading at.
 * @param v The vehicle.
 * @return The action, FerryAction::None if it is not loading at an ordered station.
 */
FerryAction GetLoadingFerryAction(const Vehicle *v)
{
	if (v->type != VehicleType::Road || !v->current_order.IsType(OT_LOADING)) return FerryAction::None;
	const Order *o = v->GetOrder(v->cur_real_order_index);
	if (o == nullptr || !o->IsType(OT_GOTO_STATION) || o->GetDestination() != v->last_station_visited) return FerryAction::None;
	return o->GetFerryAction();
}

/**
 * Get the station where a road vehicle boarding a car ferry now leaves it.
 * @param v The road vehicle.
 * @return The station of the next 'leave ship' order, or StationID::Invalid().
 */
static StationID GetFerryDestination(const Vehicle *v)
{
	std::vector<FerryOrderStop> stops = GetFerryOrderStops(v);
	std::optional<size_t> idx = FindFerryLeaveOrder(stops, v->cur_real_order_index);
	return idx.has_value() ? stops[*idx].station : StationID::Invalid();
}

/**
 * Should a road vehicle keep waiting at its stop for a car ferry?
 * @param v The vehicle.
 * @return True if it is loading at a 'board ship' order of a station with a dock, and its orders say where to leave the ship.
 */
bool IsRoadVehicleWaitingForFerry(const Vehicle *v)
{
	if (GetLoadingFerryAction(v) != FerryAction::Board) return false;
	const Station *st = Station::GetIfValid(v->last_station_visited);
	if (st == nullptr || !st->facilities.Test(StationFacility::Dock)) return false;
	StationID dest = GetFerryDestination(v);
	return dest != StationID::Invalid() && dest != v->last_station_visited;
}

static void DirtyFerryWindows(const Vehicle *ship, const Vehicle *rv)
{
	InvalidateWindowData(WindowClass::VehicleDetails, ship->index);
	SetWindowDirty(WindowClass::VehicleView, ship->index);
	SetWindowDirty(WindowClass::VehicleView, rv->index);
	SetWindowDirty(WindowClass::VehicleDetails, rv->index);
	DirtyVehicleListWindowForVehicle(ship);
	DirtyVehicleListWindowForVehicle(rv);
}

/**
 * Find where a road vehicle can drive off a car ferry at a station.
 * @param rv The road vehicle.
 * @param st The station.
 * @param[out] blocked Set when a suitable place exists but another road vehicle is in the way.
 * @return Tile and direction to drive off at, if there is a free place.
 */
static std::optional<std::pair<TileIndex, DiagDirection>> FindFerryExit(const RoadVehicle *rv, const Station *st, bool &blocked)
{
	const RoadStopType type = rv->IsBus() ? RoadStopType::Bus : RoadStopType::Truck;
	for (const RoadStop *rs = st->GetPrimaryRoadStop(type); rs != nullptr; rs = rs->next) {
		const TileIndex t = rs->xy;
		if (!HasTileAnyRoadType(t, rv->compatible_roadtypes)) continue;

		std::vector<DiagDirection> sides;
		if (IsBayRoadStopTile(t)) {
			if (rv->HasArticulatedPart()) continue;
			sides.push_back(GetBayRoadStopDir(t));
		} else {
			if (GetDriveThroughStopDisallowedRoadDirections(t) != DRD_NONE) continue;
			const DiagDirection d = AxisToDiagDir(GetDriveThroughStopAxis(t));
			sides.push_back(d);
			sides.push_back(ReverseDiagDir(d));
		}

		for (DiagDirection side : sides) {
			const TileIndex exit = TileAddByDiagDir(t, side);
			const DiagDirection dir = ReverseDiagDir(side);
			if (!RoadVehCanLeaveFerryOnto(rv, exit, dir)) continue;
			if (HasVehicleOnTile<VehicleType::Road>(exit, [](const RoadVehicle *) { return true; })) {
				blocked = true;
				continue;
			}
			return std::make_pair(exit, dir);
		}
	}
	return std::nullopt;
}

/**
 * A road vehicle drives off a car ferry.
 * @param ship The car ferry.
 * @param rv The road vehicle.
 * @param st The station the ferry is at.
 * @param exit Where it drives off.
 */
static void UnloadFromFerry(Ship *ship, RoadVehicle *rv, const Station *st, std::pair<TileIndex, DiagDirection> exit)
{
	std::erase(ship->ferry_vehicles, rv->index);
	RoadVehLeaveFerry(rv, exit.first, exit.second);

	/* Continue with the order where the vehicle leaves the ship. */
	std::vector<FerryOrderStop> stops = GetFerryOrderStops(rv);
	const size_t n = stops.size();
	for (size_t i = 0; i < n; i++) {
		const size_t idx = (rv->cur_real_order_index + i) % n;
		if (stops[idx].station == st->index && stops[idx].action == FerryAction::Leave) {
			if (idx != rv->cur_real_order_index) {
				rv->cur_implicit_order_index = rv->cur_real_order_index = static_cast<VehicleOrderID>(idx);
				rv->UpdateRealOrderIndex();
				rv->cur_timetable_order_index = INVALID_VEH_ORDER_ID;
				InvalidateVehicleOrder(rv, VIWD_MODIFY_ORDERS);
			}
			break;
		}
	}
	rv->current_order.Free();

	DirtyFerryWindows(ship, rv);
}

/**
 * A road vehicle drives onto a car ferry.
 * @param ship The car ferry.
 * @param rv The road vehicle, loading at the same station.
 */
static void BoardFerry(Ship *ship, RoadVehicle *rv)
{
	const StationID dest = GetFerryDestination(rv);

	rv->LeaveStation();
	/* Advance past the order it boarded at, like after loading. */
	const Order *order = rv->GetOrder(rv->cur_implicit_order_index);
	if (order != nullptr && (order->IsType(OT_IMPLICIT) || order->IsType(OT_GOTO_STATION)) && order->GetDestination() == rv->last_station_visited) {
		rv->IncrementImplicitOrderIndex();
	}
	rv->current_order.Free();

	RoadVehBoardFerry(rv, ship->index);
	rv->ferry_dest = dest;

	auto it = std::ranges::lower_bound(ship->ferry_vehicles, rv->index);
	ship->ferry_vehicles.insert(it, rv->index);

	DirtyFerryWindows(ship, rv);
}

static bool IsPendingFerryBoarding(VehicleID rv)
{
	return std::ranges::any_of(_pending_ferry_boardings, [&](const auto &p) { return p.second == rv; });
}

static size_t CountPendingFerryBoardings(VehicleID ship)
{
	return std::ranges::count_if(_pending_ferry_boardings, [&](const auto &p) { return p.first == ship; });
}

/**
 * Loading step of a car ferry: one road vehicle drives off or on.
 * Road vehicles first drive off, then waiting ones board.
 * @param front The ship, loading at a station.
 */
void LoadUnloadCarFerry(Vehicle *front)
{
	Ship *ship = Ship::From(front);
	Station *st = Station::Get(ship->last_station_visited);

	bool transferred = false;
	bool unload_pending = false;

	/* Drive off the road vehicles that leave the ship here. */
	const std::vector<VehicleID> aboard = ship->ferry_vehicles;
	for (VehicleID id : aboard) {
		RoadVehicle *rv = RoadVehicle::Get(id);
		if (rv->ferry_dest != st->index && Station::IsValidID(rv->ferry_dest)) continue;
		bool blocked = false;
		auto exit = FindFerryExit(rv, st, blocked);
		if (exit.has_value()) {
			UnloadFromFerry(ship, rv, st, *exit);
			transferred = true;
			break;
		}
		if (blocked) unload_pending = true;
	}

	/* Let a waiting road vehicle board. */
	const uint capacity = GetCarFerryCapacity(ship);
	if (!transferred && !unload_pending && GetFerryFreeSpaces(capacity, ship->ferry_vehicles.size() + CountPendingFerryBoardings(ship->index)) > 0) {
		const std::vector<FerryOrderStop> ship_stops = GetFerryOrderStops(ship);
		for (const Vehicle *v : st->loading_vehicles) {
			if (v->type != VehicleType::Road || !v->IsFrontEngine() || v->owner != ship->owner) continue;
			if (v->vehstatus.Any({VehState::Stopped, VehState::Crashed})) continue;
			if (!v->vehicle_flags.Test(VehicleFlag::LoadingFinished)) continue;
			if (!IsRoadVehicleWaitingForFerry(v) || IsPendingFerryBoarding(v->index)) continue;
			if (!FerryRouteCallsAt(ship_stops, GetFerryDestination(v))) continue;

			_pending_ferry_boardings.emplace_back(ship->index, v->index);
			transferred = true;
			break;
		}
	}

	/* With a full load order the ferry waits until it is full of road vehicles. */
	const bool wait_for_full = ship->current_order.IsFullLoadOrder() &&
			GetFerryFreeSpaces(capacity, ship->ferry_vehicles.size() + CountPendingFerryBoardings(ship->index)) > 0;

	ship->vehicle_flags.Set(VehicleFlag::LoadingFinished, !transferred && !unload_pending && !wait_for_full);
	ship->load_unload_ticks = FERRY_TRANSFER_TICKS;
}

/**
 * Let the road vehicles chosen during the station loading step board their car ferries.
 */
void ProcessPendingFerryBoardings()
{
	if (_pending_ferry_boardings.empty()) return;

	std::vector<std::pair<VehicleID, VehicleID>> pending = std::move(_pending_ferry_boardings);
	_pending_ferry_boardings.clear();

	for (const auto &[ship_id, rv_id] : pending) {
		Ship *ship = Ship::GetIfValid(ship_id);
		RoadVehicle *rv = RoadVehicle::GetIfValid(rv_id);
		if (ship == nullptr || rv == nullptr || !IsCarFerry(ship) || !ship->current_order.IsType(OT_LOADING)) continue;
		if (rv->last_station_visited != ship->last_station_visited || !IsRoadVehicleWaitingForFerry(rv)) continue;
		if (GetFerryFreeSpaces(GetCarFerryCapacity(ship), ship->ferry_vehicles.size()) == 0) continue;
		BoardFerry(ship, rv);
	}
}

/**
 * A car ferry is deleted: the road vehicles aboard go down with it.
 * @param ship The ship.
 */
void OnCarFerryDeleted(Vehicle *ship)
{
	const std::vector<VehicleID> aboard = std::move(Ship::From(ship)->ferry_vehicles);
	Ship::From(ship)->ferry_vehicles.clear();
	for (VehicleID id : aboard) {
		RoadVehicle *rv = RoadVehicle::GetIfValid(id);
		if (rv != nullptr && rv->ferry_carrier == ship->index) {
			for (RoadVehicle *u = rv; u != nullptr; u = u->Next()) u->ferry_carrier = VehicleID::Invalid();
			delete rv;
		}
	}
}

/**
 * A road vehicle aboard a car ferry is deleted.
 * @param rv The front of the road vehicle.
 */
void OnRoadVehicleAboardFerryDeleted(Vehicle *rv)
{
	Ship *ship = Ship::GetIfValid(RoadVehicle::From(rv)->ferry_carrier);
	if (ship == nullptr) return;
	std::erase(ship->ferry_vehicles, rv->index);
	InvalidateWindowData(WindowClass::VehicleDetails, ship->index);
}

/**
 * Rebuild the lists of road vehicles aboard car ferries after loading a game.
 */
void AfterLoadCarFerries()
{
	for (Ship *ship : Ship::Iterate()) ship->ferry_vehicles.clear();

	for (RoadVehicle *rv : RoadVehicle::Iterate()) {
		if (!rv->IsFrontEngine() || !rv->IsAboardFerry()) continue;
		Ship *ship = Ship::GetIfValid(rv->ferry_carrier);
		if (ship != nullptr) ship->ferry_vehicles.push_back(rv->index);
	}

	for (Ship *ship : Ship::Iterate()) std::ranges::sort(ship->ferry_vehicles);
}
