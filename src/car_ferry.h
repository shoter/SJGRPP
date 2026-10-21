/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file car_ferry.h Road vehicles carried by ships (car ferries). */

#ifndef CAR_FERRY_H
#define CAR_FERRY_H

#include "company_type.h"
#include "engine_type.h"
#include "order_type.h"
#include "station_type.h"
#include "vehicle_type.h"

#include <optional>
#include <span>
#include <vector>

/** One order of a vehicle, as far as car ferries are concerned. */
struct FerryOrderStop {
	StationID station;  ///< Destination of a 'go to station' order, StationID::Invalid() for any other order.
	FerryAction action; ///< What the road vehicle does with a car ferry there.
};

/**
 * Find the order where a road vehicle leaves the car ferry it boards.
 * @param orders The road vehicle's orders.
 * @param board_index Index of the order where it boards.
 * @return Index of the first 'leave ship' order after \a board_index (wrapping around), if any.
 */
std::optional<size_t> FindFerryLeaveOrder(std::span<const FerryOrderStop> orders, size_t board_index);

/**
 * Does a car ferry's route call at a station?
 * @param orders The ship's orders.
 * @param station The station.
 * @return True if one of the orders goes to \a station.
 */
bool FerryRouteCallsAt(std::span<const FerryOrderStop> orders, StationID station);

/**
 * Work out where the parts of a road vehicle are put when it drives off a car ferry.
 * The vehicle starts on a straight piece of road, the last part at the tile edge and every part
 * behind the one before it at that part's length, just like when leaving a depot.
 * @param lengths The length of each part, front first.
 * @param max_frame The last frame on the tile.
 * @return The frame of each part, front first, or std::nullopt if the vehicle does not fit on one tile.
 */
std::optional<std::vector<uint>> GetFerrySpawnFrames(std::span<const uint> lengths, uint max_frame);

/**
 * Free car spaces on a car ferry.
 * @param capacity The ship's capacity.
 * @param carried Number of road vehicles aboard.
 * @return The number of road vehicles that can still board.
 */
inline uint GetFerryFreeSpaces(uint capacity, size_t carried)
{
	return carried >= capacity ? 0 : capacity - static_cast<uint>(carried);
}

/**
 * May road vehicles of a company board a car ferry of a company?
 * Car ferries of another company can be used when infrastructure sharing for ships is on.
 * @param rv_owner Owner of the road vehicle.
 * @param ship_owner Owner of the car ferry.
 * @return True if the road vehicle may board.
 */
bool CanBoardCarFerryOf(Owner rv_owner, Owner ship_owner);

/** Maximum number of road vehicles listed in a car ferry's details window. */
static constexpr size_t MAX_CAR_FERRY_DETAILS_LINES = 8;

bool CanEngineBeCarFerry(EngineID engine);
bool CanShipBeCarFerry(const Vehicle *v);
bool IsCarFerry(const Vehicle *v);
uint GetCarFerryCapacity(const Vehicle *v);
bool IsRoadVehicleAboardFerry(const Vehicle *v);
bool IsRoadVehicleWaitingForFerry(const Vehicle *v);
FerryAction GetLoadingFerryAction(const Vehicle *v);
void LoadUnloadCarFerry(Vehicle *ship);
void ProcessPendingFerryBoardings();
void OnCarFerryDeleted(Vehicle *ship);
void OnRoadVehicleAboardFerryDeleted(Vehicle *rv);
void AfterLoadCarFerries();

#endif /* CAR_FERRY_H */
