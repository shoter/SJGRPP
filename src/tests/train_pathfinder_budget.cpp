/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file train_pathfinder_budget.cpp Test when trains pathfind and how many nodes lost trains search. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../pathfinder/train_pathfinder_budget.h"

#include "../safeguards.h"

static const TileIndex SOME_TILE{1234};

TEST_CASE("TrainPathfinderHasDestination - no order and no destination tile")
{
	CHECK_FALSE(TrainPathfinderHasDestination(OT_NOTHING, 0, INVALID_TILE));
	CHECK_FALSE(TrainPathfinderHasDestination(OT_DUMMY, 0, INVALID_TILE));
	CHECK_FALSE(TrainPathfinderHasDestination(OT_LEAVESTATION, 0, INVALID_TILE));
}

TEST_CASE("TrainPathfinderHasDestination - destination tile")
{
	CHECK(TrainPathfinderHasDestination(OT_NOTHING, 0, SOME_TILE));
	CHECK(TrainPathfinderHasDestination(OT_LEAVESTATION, 0, SOME_TILE));
	CHECK(TrainPathfinderHasDestination(OT_GOTO_DEPOT, 0, SOME_TILE));
	/* Tile 0 is a valid destination tile, unlike INVALID_TILE. */
	CHECK(TrainPathfinderHasDestination(OT_NOTHING, 0, TileIndex{0}));
}

TEST_CASE("TrainPathfinderHasDestination - stations, waypoints and nearest depot need no tile")
{
	CHECK(TrainPathfinderHasDestination(OT_GOTO_STATION, 0, INVALID_TILE));
	CHECK(TrainPathfinderHasDestination(OT_GOTO_WAYPOINT, 0, INVALID_TILE));
	CHECK(TrainPathfinderHasDestination(OT_GOTO_DEPOT, ODATFB_NEAREST_DEPOT, INVALID_TILE));
	CHECK(TrainPathfinderHasDestination(OT_GOTO_DEPOT, ODATFB_NEAREST_DEPOT | ODATFB_HALT, INVALID_TILE));
	CHECK_FALSE(TrainPathfinderHasDestination(OT_GOTO_DEPOT, ODATFB_HALT, INVALID_TILE));
}

TEST_CASE("TrainPathfinderNodeBudget - trains that are not lost get the setting")
{
	for (uint64_t day = 0; day < 16; day++) {
		CHECK(TrainPathfinderNodeBudget(10000, false, day, 3) == 10000);
	}
}

TEST_CASE("TrainPathfinderNodeBudget - lost trains get an eighth, and the full budget one day in eight")
{
	int full = 0;
	for (uint64_t day = 100; day < 100 + 8 * LOST_TRAIN_FULL_SEARCH_PERIOD; day++) {
		const int budget = TrainPathfinderNodeBudget(10000, true, day, 5);
		if ((day + 5) % LOST_TRAIN_FULL_SEARCH_PERIOD == 0) {
			CHECK(budget == 10000);
			full++;
		} else {
			CHECK(budget == 1250);
		}
	}
	CHECK(full == 8);
}

TEST_CASE("TrainPathfinderNodeBudget - full search days are spread over the trains")
{
	/* On any day exactly one train in eight consecutive indices gets the full budget. */
	for (uint64_t day = 0; day < LOST_TRAIN_FULL_SEARCH_PERIOD; day++) {
		int full = 0;
		for (uint32_t index = 0; index < LOST_TRAIN_FULL_SEARCH_PERIOD; index++) {
			if (TrainPathfinderNodeBudget(2000, true, day, index) == 2000) full++;
		}
		CHECK(full == 1);
	}
}

TEST_CASE("TrainPathfinderNodeBudget - limits")
{
	/* 0 means no limit, also for lost trains. */
	CHECK(TrainPathfinderNodeBudget(0, true, 1, 0) == 0);
	/* Never 0 (which would mean no limit) for a small setting. */
	CHECK(TrainPathfinderNodeBudget(5, true, 1, 0) == 1);
	CHECK(TrainPathfinderNodeBudget(16, true, 1, 0) == 2);
}
