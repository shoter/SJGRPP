/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file station_near_cache.cpp Test the catchment area filter of the town nearby station cache. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../station_container.h"

#include <random>
#include <set>
#include <vector>

#include "../safeguards.h"

/** Fake station pointer; the filter never dereferences it. */
static Station *FakeStation(uintptr_t id)
{
	return reinterpret_cast<Station *>((id + 1) * 64);
}

TEST_CASE("StationNearCacheEntry - area bounds")
{
	StationNearCacheEntry e{ 10, 20, 5, 3, FakeStation(0) };

	/* Corners and edges, inclusive at the north side, exclusive at x + w and y + h. */
	CHECK(e.AreaContains(10, 20));
	CHECK(e.AreaContains(14, 22));
	CHECK(e.AreaContains(14, 20));
	CHECK(e.AreaContains(10, 22));
	CHECK_FALSE(e.AreaContains(15, 20));
	CHECK_FALSE(e.AreaContains(10, 23));
	CHECK_FALSE(e.AreaContains(9, 20));
	CHECK_FALSE(e.AreaContains(10, 19));

	/* Coordinates far below the corner must not wrap into the area. */
	CHECK_FALSE(e.AreaContains(0, 0));
	CHECK_FALSE(e.AreaContains(0, 21));

	/* An empty (reset) catchment contains nothing. */
	StationNearCacheEntry empty{ 0, 0, 0, 0, FakeStation(1) };
	CHECK_FALSE(empty.AreaContains(0, 0));

	/* Map edge at the maximum map size. */
	StationNearCacheEntry edge{ (1u << 20) - 4, 0, 4, 4, FakeStation(2) };
	CHECK(edge.AreaContains((1u << 20) - 1, 3));
	CHECK_FALSE(edge.AreaContains(1u << 20, 3));
}

TEST_CASE("FilterStationNearCache - only tests catchment inside the area, keeps order")
{
	std::vector<StationNearCacheEntry> cache = {
		{ 0, 0, 10, 10, FakeStation(1) },
		{ 5, 5, 10, 10, FakeStation(2) },
		{ 20, 20, 4, 4, FakeStation(3) },
		{ 4, 4, 3, 3, FakeStation(4) },
	};

	std::vector<Station *> tested;
	std::vector<Station *> out;
	FilterStationNearCache(cache, 6, 6, [&](Station *st) {
		tested.push_back(st);
		return st != FakeStation(2); // tile is inside station 2's area but not its catchment
	}, out);

	CHECK(tested == std::vector<Station *>{ FakeStation(1), FakeStation(2), FakeStation(4) });
	CHECK(out == std::vector<Station *>{ FakeStation(1), FakeStation(4) });

	/* Nothing contains this tile, so the catchment is never tested. */
	tested.clear();
	out.clear();
	FilterStationNearCache(cache, 30, 2, [&](Station *st) { tested.push_back(st); return true; }, out);
	CHECK(tested.empty());
	CHECK(out.empty());

	/* Results are appended, not replacing what is already there. */
	StationFinderList list;
	list.push_back(FakeStation(0));
	FilterStationNearCache(cache, 21, 21, [](Station *) { return true; }, list);
	REQUIRE(list.size() == 2);
	CHECK(list[0] == FakeStation(0));
	CHECK(list[1] == FakeStation(3));
}

TEST_CASE("FilterStationNearCache - same result as testing every station")
{
	/* Random stations with sparse catchments; compare the filter with a plain loop over all stations. */
	std::mt19937 rng(12345);
	constexpr uint MAP = 64;
	std::vector<StationNearCacheEntry> cache;
	std::vector<std::set<std::pair<uint, uint>>> catchments;
	for (uint i = 0; i < 200; i++) {
		uint w = 1 + rng() % 12;
		uint h = 1 + rng() % 12;
		uint x = rng() % (MAP - w);
		uint y = rng() % (MAP - h);
		cache.push_back({ x, y, w, h, FakeStation(i) });
		std::set<std::pair<uint, uint>> tiles;
		for (uint ty = y; ty < y + h; ty++) {
			for (uint tx = x; tx < x + w; tx++) {
				if (rng() % 3 != 0) tiles.insert({ tx, ty });
			}
		}
		catchments.push_back(std::move(tiles));
	}

	auto index_of = [](Station *st) { return reinterpret_cast<uintptr_t>(st) / 64 - 1; };

	for (uint ty = 0; ty < MAP; ty++) {
		for (uint tx = 0; tx < MAP; tx++) {
			StationFinderList filtered;
			FilterStationNearCache(cache, tx, ty, [&](Station *st) { return catchments[index_of(st)].contains({ tx, ty }); }, filtered);

			std::vector<Station *> expected;
			for (uint i = 0; i < cache.size(); i++) {
				if (catchments[i].contains({ tx, ty })) expected.push_back(cache[i].st);
			}
			REQUIRE(std::vector<Station *>(filtered.begin(), filtered.end()) == expected);
		}
	}
}
