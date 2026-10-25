/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file vehicle_tile_occupancy.cpp Test the vehicle tile occupancy bitmap and its maintenance by the vehicle tile hash. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../vehicle_tile_occupancy.h"
#include "../map_func.h"
#include "../ship.h"
#include "../vehicle_func.h"
#include "../core/random_func.hpp"

#include <map>
#include <set>

#include "../safeguards.h"

extern void UpdateVehicleTileHash(Vehicle *v, bool remove);
extern bool ValidateVehicleTileOccupancy(std::function<void(std::string_view)> log);
extern bool ValidateVehicleTileHash(const Vehicle *v);

TEST_CASE("VehicleTileOccupancy - set and clear single bits")
{
	VehicleTileOccupancy occ;
	occ.Reset(200);
	CHECK(occ.Size() == 200);
	for (uint i = 0; i < 200; i++) CHECK_FALSE(occ.MayBeOccupied(TileIndex{i}));

	/* Word boundaries: 63/64 and the last tile. */
	for (uint i : {0u, 63u, 64u, 127u, 128u, 199u}) {
		occ.Set(TileIndex{i});
		CHECK(occ.MayBeOccupied(TileIndex{i}));
	}
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{1}));
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{62}));
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{65}));

	occ.Clear(TileIndex{64});
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{64}));
	CHECK(occ.MayBeOccupied(TileIndex{63}));

	/* Setting twice, clearing twice. */
	occ.Set(TileIndex{5});
	occ.Set(TileIndex{5});
	CHECK(occ.MayBeOccupied(TileIndex{5}));
	occ.Clear(TileIndex{5});
	occ.Clear(TileIndex{5});
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{5}));

	occ.Reset(200);
	for (uint i = 0; i < 200; i++) CHECK_FALSE(occ.MayBeOccupied(TileIndex{i}));
}

TEST_CASE("VehicleTileOccupancy - tiles outside the bitmap may always be occupied")
{
	VehicleTileOccupancy occ;
	/* An unsized bitmap never answers "empty": lookups fall back to the hash. */
	CHECK(occ.MayBeOccupied(TileIndex{0}));
	occ.Reset(64);
	occ.Set(TileIndex{64});
	occ.Clear(TileIndex{100});
	CHECK(occ.MayBeOccupied(TileIndex{64}));
	CHECK(occ.MayBeOccupied(TileIndex{0xFFFFFFF0}));
	CHECK_FALSE(occ.MayBeOccupied(TileIndex{63}));
}

TEST_CASE("VehicleTileOccupancy - random insert/erase follows a key set")
{
	VehicleTileOccupancy occ;
	const uint tiles = 4096;
	occ.Reset(tiles);
	std::set<uint> keys;
	Randomizer rnd;
	rnd.SetSeed(1234);
	for (int i = 0; i < 20000; i++) {
		const uint t = rnd.Next(tiles);
		if (rnd.Next(2) == 0) {
			occ.Set(TileIndex{t});
			keys.insert(t);
		} else {
			occ.Clear(TileIndex{t});
			keys.erase(t);
		}
	}
	VehicleTileOccupancy rebuilt;
	rebuilt.Reset(tiles);
	for (uint t : keys) rebuilt.Set(TileIndex{t});
	CHECK(rebuilt == occ);
	for (uint t = 0; t < tiles; t++) CHECK(occ.MayBeOccupied(TileIndex{t}) == (keys.count(t) != 0));
}

/** Vehicle pool and map set up for one test, cleaned afterwards. */
struct VehicleHashFixture {
	VehicleHashFixture()
	{
		AllocateMap(64, 64);
		_vehicle_pool.CleanPool();
		ResetVehicleHash();
	}

	~VehicleHashFixture()
	{
		_vehicle_pool.CleanPool();
		ResetVehicleHash();
	}

	static Ship *NewShip(TileIndex tile)
	{
		REQUIRE(Ship::CanAllocateItem()); // debug builds assert that the pool was checked before allocating
		Ship *v = Ship::Create();
		v->tile = tile;
		UpdateVehicleTileHash(v, false);
		return v;
	}

	static void Move(Vehicle *v, TileIndex tile)
	{
		v->tile = tile;
		UpdateVehicleTileHash(v, false);
	}

	static size_t CountOnTile(TileIndex tile, VehicleType type)
	{
		size_t n = 0;
		for (const Vehicle *v = GetFirstVehicleOnTileFromHash(tile, type); v != nullptr; v = v->HashTileNext()) n++;
		return n;
	}

	/** Every tile: the bitmap says empty exactly when the hash has no vehicle, and both lookups agree. */
	static void CheckConsistent()
	{
		std::vector<std::string> errors;
		CHECK(ValidateVehicleTileOccupancy([&](std::string_view s) { errors.emplace_back(s); }));
		CHECK(errors.empty());
		for (TileIndex tile(0); tile < Map::Size(); ++tile) {
			for (VehicleType type : {VehicleType::Train, VehicleType::Road, VehicleType::Ship, VehicleType::Aircraft}) {
				const Vehicle *from_hash = GetFirstVehicleOnTileFromHash(tile, type);
				CHECK(_vehicle_tile_occupancy[type].MayBeOccupied(tile) == (from_hash != nullptr));
				CHECK(GetFirstVehicleOnTile(tile, type) == from_hash);
			}
		}
		/* Vehicles taken out of the hash by the tests still have their tile, which the validation would reject. */
		for (const Vehicle *v : Vehicle::Iterate()) {
			if (v->hash_tile_current != INVALID_TILE) CHECK(ValidateVehicleTileHash(v));
		}
	}
};

TEST_CASE_METHOD(VehicleHashFixture, "VehicleTileOccupancy - bitmap is sized to the map on hash reset")
{
	for (VehicleType type : {VehicleType::Train, VehicleType::Road, VehicleType::Ship, VehicleType::Aircraft}) {
		CHECK(_vehicle_tile_occupancy[type].Size() == Map::Size());
	}
	CheckConsistent();
}

TEST_CASE_METHOD(VehicleHashFixture, "VehicleTileOccupancy - insert, share, move and remove")
{
	const TileIndex a = TileXY(3, 4);
	const TileIndex b = TileXY(10, 20);

	Ship *v1 = NewShip(a);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(a));
	CHECK_FALSE(_vehicle_tile_occupancy[VehicleType::Train].MayBeOccupied(a));
	CHECK(GetFirstVehicleOnTile(a, VehicleType::Ship) == v1);
	CheckConsistent();

	/* A second vehicle on the same tile becomes the chain head; the bit stays set. */
	Ship *v2 = NewShip(a);
	CHECK(GetFirstVehicleOnTile(a, VehicleType::Ship) == v2);
	CHECK(CountOnTile(a, VehicleType::Ship) == 2);
	CheckConsistent();

	/* Moving the head away leaves the other vehicle: the bit must stay set. */
	Move(v2, b);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(a));
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(b));
	CHECK(GetFirstVehicleOnTile(a, VehicleType::Ship) == v1);
	CheckConsistent();

	/* Moving the last vehicle away clears the bit. */
	Move(v1, b);
	CHECK_FALSE(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(a));
	CHECK(GetFirstVehicleOnTile(a, VehicleType::Ship) == nullptr);
	CHECK(CountOnTile(b, VehicleType::Ship) == 2);
	CheckConsistent();

	/* Removing a non-head vehicle keeps the bit, removing the last one clears it. */
	UpdateVehicleTileHash(v2, true);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(b));
	CheckConsistent();
	UpdateVehicleTileHash(v1, true);
	CHECK_FALSE(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(b));
	CheckConsistent();

	/* The near-tile iterator agrees: nothing left anywhere. */
	CHECK_FALSE(HasVehicleNearTileXY(TileX(b) * TILE_SIZE, TileY(b) * TILE_SIZE, 16, VehicleType::Ship, [](const Vehicle *) { return true; }));
}

TEST_CASE_METHOD(VehicleHashFixture, "VehicleTileOccupancy - near-tile iteration finds vehicles in the scanned area")
{
	const TileIndex t = TileXY(30, 30);
	Ship *v = NewShip(t);
	v->x_pos = TileX(t) * TILE_SIZE + 8;
	v->y_pos = TileY(t) * TILE_SIZE + 8;
	/* The scanned area starts on the empty neighbouring tiles and ends on the vehicle's tile. */
	size_t found = 0;
	for (const Vehicle *u : VehiclesNearTileXY(v->x_pos - 4, v->y_pos - 4, 8, VehicleType::Ship)) {
		CHECK(u == v);
		found++;
	}
	CHECK(found == 1);
	found = 0;
	for ([[maybe_unused]] const Vehicle *u : VehiclesNearTileXY(v->x_pos - 64, v->y_pos - 64, 8, VehicleType::Ship)) found++;
	CHECK(found == 0);
	CheckConsistent();
}

TEST_CASE_METHOD(VehicleHashFixture, "VehicleTileOccupancy - random moves keep bitmap and hash in sync")
{
	std::vector<Ship *> ships;
	Randomizer rnd;
	rnd.SetSeed(42);
	/* A small area so that many vehicles share tiles and chain heads change often. */
	auto random_tile = [&]() { return TileXY(1 + rnd.Next(6), 1 + rnd.Next(6)); };
	for (int i = 0; i < 40; i++) ships.push_back(NewShip(random_tile()));
	CheckConsistent();

	std::map<Ship *, bool> in_hash;
	for (Ship *v : ships) in_hash[v] = true;
	for (int step = 0; step < 3000; step++) {
		Ship *v = ships[rnd.Next(static_cast<uint32_t>(ships.size()))];
		if (rnd.Next(5) == 0) {
			UpdateVehicleTileHash(v, in_hash[v]);
			in_hash[v] = !in_hash[v];
		} else if (in_hash[v]) {
			Move(v, random_tile());
		}
		if (step % 500 == 0) CheckConsistent();
	}
	CheckConsistent();

	/* A full reset (load, new game) clears all bits; re-inserting rebuilds them. */
	ResetVehicleHash();
	CheckConsistent();
	for (Ship *v : ships) UpdateVehicleTileHash(v, false);
	CheckConsistent();
}

TEST_CASE_METHOD(VehicleHashFixture, "VehicleTileOccupancy - bitmap follows a map reallocation")
{
	/* Loading a savegame allocates the map of the save after the hash was reset for the previous map. */
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].Size() == 64 * 64);
	AllocateMap(256, 128);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].Size() == 256 * 128);
	CheckConsistent();

	/* Keys already in the hash keep their bits. */
	const TileIndex t = TileXY(200, 100);
	Ship *v = NewShip(t);
	AllocateMap(256, 256);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].Size() == 256 * 256);
	CHECK(_vehicle_tile_occupancy[VehicleType::Ship].MayBeOccupied(t));
	CHECK(GetFirstVehicleOnTile(t, VehicleType::Ship) == v);
	CheckConsistent();
}
