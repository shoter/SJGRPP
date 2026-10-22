/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file train_pathfinder_budget.h How much pathfinding trains without a destination and lost trains get. */

#ifndef TRAIN_PATHFINDER_BUDGET_H
#define TRAIN_PATHFINDER_BUDGET_H

#include "../order_type.h"
#include "../tile_type.h"

/** A lost train searches with max_search_nodes divided by this. */
static constexpr int LOST_TRAIN_NODE_DIVISOR = 8;
/** A lost train gets a full search one day (of DAY_TICKS) in this many, offset by its vehicle index. */
static constexpr uint LOST_TRAIN_FULL_SEARCH_PERIOD = 8;

/**
 * Does the rail pathfinder have something to look for?
 * Mirrors the destination YAPF picks for a train: stations, waypoints and "nearest depot" are always targets,
 * everything else uses the destination tile. Without one YAPF would look for tile 0, which can never be reached,
 * so it would search the whole network (up to max_search_nodes) at every junction.
 * @param type Type of the train's current order.
 * @param depot_action_type Depot action flags of the current order (only used for OT_GOTO_DEPOT).
 * @param dest_tile The train's destination tile.
 * @return true iff the pathfinder has a destination.
 */
inline bool TrainPathfinderHasDestination(OrderType type, uint depot_action_type, TileIndex dest_tile)
{
	switch (type) {
		case OT_GOTO_STATION:
		case OT_GOTO_WAYPOINT:
			return true;

		case OT_GOTO_DEPOT:
			if (depot_action_type & ODATFB_NEAREST_DEPOT) return true;
			break;

		default:
			break;
	}
	return dest_tile != INVALID_TILE;
}

/**
 * Node budget for a train pathfinder search.
 * Lost trains search with a fraction of the budget, except for one day in LOST_TRAIN_FULL_SEARCH_PERIOD (spread
 * over the trains by their index), when they get the whole budget so they find a route that appeared again.
 * All inputs are synced game state, so every client in a network game uses the same budget.
 * @param max_search_nodes The max_search_nodes setting (0 means no limit).
 * @param lost Whether the train is flagged as lost.
 * @param day Game ticks divided by DAY_TICKS.
 * @param vehicle_index Index of the train.
 * @return The maximum number of nodes for the search (0 means no limit).
 */
inline int TrainPathfinderNodeBudget(int max_search_nodes, bool lost, uint64_t day, uint32_t vehicle_index)
{
	if (!lost || max_search_nodes <= 0) return max_search_nodes;
	if ((day + vehicle_index) % LOST_TRAIN_FULL_SEARCH_PERIOD == 0) return max_search_nodes;
	return std::max(1, max_search_nodes / LOST_TRAIN_NODE_DIVISOR);
}

#endif /* TRAIN_PATHFINDER_BUDGET_H */
