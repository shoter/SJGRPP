/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector.cpp Handling of sectors: regions of the map where companies need building rights. */

#include "stdafx.h"
#include "sector_base.h"
#include "sector_func.h"
#include "company_base.h"
#include "company_func.h"
#include "core/pool_func.hpp"
#include "core/random_func.hpp"
#include "economy_func.h"
#include "gfx_func.h"
#include "map_func.h"
#include "openttd.h"
#include "settings_type.h"
#include "strings_func.h"
#include "tile_cmd.h"
#include "tile_map.h"
#include "town.h"
#include "viewport_func.h"
#include "water_map.h"
#include "window_func.h"

#include "table/sprites.h"
#include "table/strings.h"

#include "safeguards.h"

SectorPool _sector_pool("Sector");
INSTANTIATE_POOL_METHODS(Sector)

/** Sector of each tile, empty when the map has no sectors. */
std::vector<uint8_t> _sector_map;

/** Whether sector borders are drawn in the viewports. */
bool _show_sector_overlay = false;

/** Building rights are not checked, e.g. while the items of a removed company are cleaned up. */
bool _sector_checks_suspended = false;

/** Sector highlighted in the viewports, e.g. the one selected in the sector window. */
SectorID _highlighted_sector = SectorID::Invalid();

Sector::~Sector()
{
	if (CleaningPool()) return;
	if (_highlighted_sector == this->index) _highlighted_sector = SectorID::Invalid();
	InvalidateWindowData(WindowClass::Sectors, 0);
}

/**
 * Get the price of the building rights before inflation.
 * @return The price.
 */
Money Sector::GetBasePrice() const
{
	if (this->custom_price > 0) return this->custom_price;
	return static_cast<Money>(this->tile_count) * _settings_game.economy.sector_price_per_tile;
}

/**
 * Get the price of the building rights, including inflation.
 * @return The price.
 */
Money Sector::GetPrice() const
{
	Money price = this->GetBasePrice();
	return price * static_cast<int64_t>(_economy.inflation_prices) >> 16;
}

/**
 * Get the price of the building rights for a company, taking the free first sector into account.
 * @param company The company buying the rights.
 * @return The price.
 */
Money Sector::GetPriceFor(CompanyID company) const
{
	if (!this->no_first_pick && !CompanyHasAnySectorRights(company)) return 0;
	return this->GetPrice();
}

/**
 * Are sectors enabled in the current game?
 * @return True when sectors are enabled and the map is divided into sectors.
 */
bool AreSectorsEnabled()
{
	return _settings_game.economy.sectors && !_sector_map.empty();
}

/**
 * Does the map have sector data (also in the scenario editor, regardless of the setting)?
 * @return True when the map is divided into sectors.
 */
bool MapHasSectors()
{
	return !_sector_map.empty();
}

/** Forget the sector layer, e.g. because a new map is allocated. */
void InitializeSectorMap()
{
	_sector_map.clear();
	_sector_map.shrink_to_fit();
	_highlighted_sector = SectorID::Invalid();
}

/** Make sure the sector layer exists, all tiles without a sector. */
void EnsureSectorMap()
{
	if (_sector_map.size() != Map::Size()) _sector_map.assign(Map::Size(), SectorID::Invalid().base());
}

/**
 * Set the sector of a tile.
 * @param tile The tile.
 * @param sector The new sector.
 */
void SetTileSector(TileIndex tile, SectorID sector)
{
	EnsureSectorMap();
	_sector_map[tile.base()] = sector.base();
}

/**
 * Does a company own building rights in any sector?
 * @param company The company.
 * @return True if it owns at least one sector.
 */
bool CompanyHasAnySectorRights(CompanyID company)
{
	for (const Sector *s : Sector::Iterate()) {
		if (s->HasRights(company)) return true;
	}
	return false;
}

/**
 * Get the name of a sector.
 * @param sector The sector.
 * @return The custom name, the name of its town, or a numbered name.
 */
std::string GetSectorName(SectorID sector)
{
	const Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr) return GetString(STR_SECTOR_NAME_NONE);
	if (!s->name.empty()) return s->name;
	if (Town::IsValidID(s->town)) return GetString(STR_SECTOR_NAME_TOWN, s->town);
	return GetString(STR_SECTOR_NAME_NUMBER, sector.base() + 1);
}

/** Recalculate the tile counts and centres of all sectors. */
void UpdateSectorCaches()
{
	if (_sector_map.empty()) return;

	struct Accumulator {
		uint64_t sum_x = 0;
		uint64_t sum_y = 0;
		uint32_t count = 0;
		uint64_t best_dist = UINT64_MAX;
	};
	std::vector<Accumulator> acc(SectorID::End().base());

	const uint size = Map::Size();
	for (uint i = 0; i < size; i++) {
		uint8_t s = _sector_map[i];
		if (s == SectorID::Invalid().base()) continue;
		acc[s].sum_x += TileX(TileIndex(i));
		acc[s].sum_y += TileY(TileIndex(i));
		acc[s].count++;
	}

	for (Sector *s : Sector::Iterate()) {
		s->tile_count = acc[s->index.base()].count;
		s->centre = INVALID_TILE;
	}

	/* Find the tile of each sector which is nearest to its centre of mass, so the centre is always inside the sector. */
	for (uint i = 0; i < size; i++) {
		uint8_t s = _sector_map[i];
		if (s == SectorID::Invalid().base()) continue;
		Accumulator &a = acc[s];
		if (a.count == 0) continue;
		int64_t dx = static_cast<int64_t>(TileX(TileIndex(i))) * a.count - static_cast<int64_t>(a.sum_x);
		int64_t dy = static_cast<int64_t>(TileY(TileIndex(i))) * a.count - static_cast<int64_t>(a.sum_y);
		uint64_t dist = static_cast<uint64_t>(std::abs(dx)) + static_cast<uint64_t>(std::abs(dy));
		if (dist < a.best_dist) {
			a.best_dist = dist;
			Sector *sec = Sector::GetIfValid(SectorID(s));
			if (sec != nullptr) sec->centre = TileIndex(i);
		}
	}

	InvalidateWindowData(WindowClass::Sectors, 0);
}

/**
 * Name sectors after the biggest town inside them.
 * @param only_missing Only assign a town to sectors which have no (valid) town yet.
 */
void AssignSectorTowns(bool only_missing)
{
	if (_sector_map.empty()) return;

	std::vector<const Town *> best(SectorID::End().base(), nullptr);
	for (const Town *t : Town::Iterate()) {
		SectorID s = GetTileSector(t->xy);
		if (s == SectorID::Invalid()) continue;
		const Town *&b = best[s.base()];
		if (b == nullptr || t->cache.population > b->cache.population) b = t;
	}

	for (Sector *s : Sector::Iterate()) {
		if (only_missing && Town::IsValidID(s->town)) continue;
		const Town *t = best[s->index.base()];
		s->town = (t != nullptr) ? t->index : TownID::Invalid();
	}

	InvalidateWindowData(WindowClass::Sectors, 0);
}

/**
 * Remove all building rights of a company, e.g. because it went bankrupt or was taken over.
 * @param company The company.
 */
void ClearSectorRightsOfCompany(CompanyID company)
{
	bool changed = false;
	for (Sector *s : Sector::Iterate()) {
		if (s->HasRights(company)) {
			s->rights.Reset(company);
			changed = true;
		}
	}
	if (changed) {
		InvalidateWindowData(WindowClass::Sectors, 0);
		MarkSectorOverlayDirty();
	}
}

/**
 * Divide the map randomly into sectors.
 * Sectors grow from evenly spread seeds. Growing is cheaper across land in some (random) areas than
 * in others, and expensive across height differences and coast lines, so borders tend to follow hills and shores.
 * @param average_size The average number of tiles of a sector.
 */
void GenerateSectors(uint32_t average_size)
{
	_sector_pool.CleanPool();
	_highlighted_sector = SectorID::Invalid();
	_sector_map.assign(Map::Size(), SectorID::Invalid().base());

	const uint size = Map::Size();
	const uint count = Clamp<uint>(size / std::max<uint32_t>(average_size, 1), 1, SectorID::End().base());

	/* Spread the seeds with best candidate sampling. */
	std::vector<TileIndex> seeds;
	seeds.reserve(count);
	for (uint i = 0; i < count; i++) {
		TileIndex best = INVALID_TILE;
		uint best_dist = 0;
		for (uint attempt = 0; attempt < 12; attempt++) {
			TileIndex candidate = TileXY(1 + RandomRange(Map::SizeX() - 2), 1 + RandomRange(Map::SizeY() - 2));
			uint dist = UINT_MAX;
			for (TileIndex seed : seeds) dist = std::min(dist, DistanceSquare(seed, candidate));
			if (dist == 0) continue;
			if (best == INVALID_TILE || dist > best_dist) {
				best = candidate;
				best_dist = dist;
			}
		}
		if (best == INVALID_TILE) continue;
		seeds.push_back(best);
	}

	/* Random growth cost per 16x16 block, making borders irregular. */
	const uint blocks_x = (Map::SizeX() + 15) / 16;
	const uint blocks_y = (Map::SizeY() + 15) / 16;
	std::vector<uint8_t> block_cost(blocks_x * blocks_y);
	for (uint8_t &c : block_cost) c = 4 + RandomRange(12);

	/* Dial's algorithm: buckets of tiles by distance, modulo one more than the maximum step cost. */
	static constexpr uint MAX_STEP = (4 + 11) + 3 + 4 * 8 + 16;
	struct Item {
		uint32_t tile;
		uint8_t sector;
	};
	std::array<std::vector<Item>, MAX_STEP + 1> buckets;
	size_t queued = 0;

	for (TileIndex seed : seeds) {
		Sector *s = Sector::Create();
		buckets[0].push_back({ seed.base(), s->index.base() });
		queued++;
	}

	auto step_cost = [&](TileIndex from, TileIndex to) -> uint {
		uint cost = block_cost[(TileY(to) / 16) * blocks_x + TileX(to) / 16] + RandomRange(4);
		int dh = static_cast<int>(TileHeight(from)) - static_cast<int>(TileHeight(to));
		cost += std::min<uint>(std::abs(dh), 4) * 8;
		if (IsWaterTile(from) != IsWaterTile(to)) cost += 16;
		return cost;
	};

	static const TileIndexDiffC neighbours[] = { {1, 0}, {-1, 0}, {0, 1}, {0, -1} };

	for (uint distance = 0; queued > 0; distance++) {
		std::vector<Item> &bucket = buckets[distance % buckets.size()];
		while (!bucket.empty()) {
			Item item = bucket.back();
			bucket.pop_back();
			queued--;
			if (_sector_map[item.tile] != SectorID::Invalid().base()) continue;
			_sector_map[item.tile] = item.sector;

			TileIndex tile(item.tile);
			for (const TileIndexDiffC &diff : neighbours) {
				int x = static_cast<int>(TileX(tile)) + diff.x;
				int y = static_cast<int>(TileY(tile)) + diff.y;
				if (x < 0 || y < 0 || x >= static_cast<int>(Map::SizeX()) || y >= static_cast<int>(Map::SizeY())) continue;
				TileIndex next = TileXY(x, y);
				if (_sector_map[next.base()] != SectorID::Invalid().base()) continue;
				buckets[(distance + step_cost(tile, next)) % buckets.size()].push_back({ next.base(), item.sector });
				queued++;
			}
		}
	}

	UpdateSectorCaches();
	for (Sector *s : Sector::Iterate()) {
		if (s->tile_count == 0) delete s;
	}
	AssignSectorTowns(false);
	MarkSectorOverlayDirty();
	InvalidateWindowData(WindowClass::Sectors, 0);
}

/** Generate sectors for a game which has sectors enabled but whose map has none (e.g. a scenario without sectors). */
void GenerateSectorsIfMissing()
{
	if (!_settings_game.economy.sectors) return;
	if (MapHasSectors() && Sector::GetNumItems() > 0) {
		AssignSectorTowns(true);
		return;
	}
	GenerateSectors(_settings_game.economy.sector_size);
}

/** Restore the caches after loading a game. */
void AfterLoadSectors()
{
	_highlighted_sector = SectorID::Invalid();
	if (!_sector_map.empty() && _sector_map.size() != Map::Size()) _sector_map.clear();

	if (_sector_map.empty()) {
		_sector_pool.CleanPool();
		return;
	}

	/* Tiles may only refer to existing sectors. */
	for (uint8_t &s : _sector_map) {
		if (s != SectorID::Invalid().base() && !Sector::IsValidID(SectorID(s))) s = SectorID::Invalid().base();
	}

	for (Sector *s : Sector::Iterate()) {
		if (!Town::IsValidID(s->town)) s->town = TownID::Invalid();
	}

	UpdateSectorCaches();
}

/**
 * Do sector restrictions apply to the current company?
 * @return True when the current company needs building rights.
 */
static bool SectorRestrictionsApply()
{
	return _game_mode == GameMode::Normal && !_sector_checks_suspended && AreSectorsEnabled() && Company::IsValidID(_current_company);
}

/**
 * Make the error for a tile without building rights.
 * @param sector The sector of the tile.
 * @return The error.
 */
static CommandCost SectorRightsError(SectorID sector)
{
	CommandCost error(STR_ERROR_SECTOR_NO_BUILDING_RIGHTS);
	if (IsLocalCompany()) {
		error.SetEncodedMessage(GetEncodedString(STR_ERROR_SECTOR_NO_BUILDING_RIGHTS_IN, GetSectorName(sector)));
	}
	return error;
}

/**
 * Check whether the current company may build on a tile.
 * @param tile The tile.
 * @return Success, or an error when the company has no building rights in the sector of the tile.
 */
CommandCost CheckSectorBuildingRights(TileIndex tile)
{
	if (!SectorRestrictionsApply()) return CommandCost();
	if (tile >= Map::Size()) return CommandCost();

	SectorID sector = GetTileSector(tile);
	const Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr || s->HasRights(_current_company)) return CommandCost();
	return SectorRightsError(sector);
}

/**
 * Check whether the current company may build on all tiles of an area.
 * @param area The area.
 * @return Success, or an error when the company has no building rights in the sector of one of the tiles.
 */
CommandCost CheckSectorBuildingRights(const TileArea &area)
{
	if (!SectorRestrictionsApply()) return CommandCost();

	for (TileIndex tile : area) {
		CommandCost ret = CheckSectorBuildingRights(tile);
		if (ret.Failed()) return ret;
	}
	return CommandCost();
}

/**
 * Check whether the current company may build on all tiles of a straight line, e.g. a bridge or tunnel.
 * @param begin The first tile.
 * @param end The last tile, on the same row or column as \p begin.
 * @return Success, or an error when the company has no building rights in the sector of one of the tiles.
 */
CommandCost CheckSectorBuildingRightsLine(TileIndex begin, TileIndex end)
{
	if (!SectorRestrictionsApply()) return CommandCost();
	return CheckSectorBuildingRights(TileArea(begin, end));
}

/** Redraw the viewports if the sector overlay is visible. */
/** Tiles of the outline being drawn with the lasso tool in the scenario editor, empty when none is drawn. */
std::vector<bool> _sector_lasso_mask;

void MarkSectorOverlayDirty()
{
	if (_show_sector_overlay) MarkWholeScreenDirty();
}

/**
 * Is a tile at the border of its sector?
 * @param tile The tile.
 * @param sector The sector of the tile.
 * @return True if a neighbouring tile belongs to another sector.
 */
static bool IsSectorBorderTile(TileIndex tile, SectorID sector)
{
	const uint x = TileX(tile);
	const uint y = TileY(tile);
	if (x > 0 && GetTileSector(TileXY(x - 1, y)) != sector) return true;
	if (y > 0 && GetTileSector(TileXY(x, y - 1)) != sector) return true;
	if (x + 1 < Map::SizeX() && GetTileSector(TileXY(x + 1, y)) != sector) return true;
	if (y + 1 < Map::SizeY() && GetTileSector(TileXY(x, y + 1)) != sector) return true;
	return false;
}

/**
 * Draw the sector overlay on a tile: the border tiles of every sector are highlighted,
 * in green where the local company has building rights and in red where it has not.
 * @param ti The tile being drawn.
 */
void DrawTileSectorOverlay(const TileInfo *ti)
{
	if (!_sector_lasso_mask.empty() && _sector_lasso_mask[ti->tile.base()]) DrawTileSelectionRect(ti, PALETTE_SEL_TILE_RED);
	if (!_show_sector_overlay || _sector_map.empty()) return;
	if (_game_mode != GameMode::Normal && _game_mode != GameMode::Editor) return;

	SectorID sector = GetTileSector(ti->tile);
	const Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr) return;

	if (sector == _highlighted_sector) DrawTileSelectionRect(ti, PALETTE_SEL_TILE_BLUE);

	if (!IsSectorBorderTile(ti->tile, sector)) return;

	PaletteID colour;
	if (_game_mode == GameMode::Editor) {
		colour = s->no_first_pick ? SPR_ZONING_INNER_HIGHLIGHT_ORANGE : SPR_ZONING_INNER_HIGHLIGHT_WHITE;
	} else if (!Company::IsValidID(_local_company)) {
		colour = s->rights.Any() ? SPR_ZONING_INNER_HIGHLIGHT_LIGHT_BLUE : SPR_ZONING_INNER_HIGHLIGHT_WHITE;
	} else if (s->HasRights(_local_company)) {
		colour = SPR_ZONING_INNER_HIGHLIGHT_GREEN;
	} else {
		colour = SPR_ZONING_INNER_HIGHLIGHT_RED;
	}

	SpriteID sprite = SPR_ZONING_INNER_HIGHLIGHT_BASE;
	if (IsHalftileSlope(ti->tileh)) {
		const int INF = 1000;
		static const SubSprite sub_sprites[4] = {
			{ -INF    , -INF   , 32 - 33, INF     }, // CORNER_W, clip 33 pixels from right
			{ -INF    ,  0 + 22, INF    , INF     }, // CORNER_S, clip 22 pixels from top
			{ -31 + 34, -INF   , INF    , INF     }, // CORNER_E, clip 34 pixels from left
			{ -INF    , -INF   , INF    , 30 - 8  }  // CORNER_N, clip  8 pixels from bottom
		};
		DrawSelectionSprite(sprite, colour, ti, 7 + TILE_HEIGHT, FoundationPart::Halftile, 0, 0, &(sub_sprites[GetHalftileSlopeCorner(ti->tileh)]));
	} else {
		sprite += SlopeToSpriteOffset(ti->tileh);
	}
	DrawSelectionSprite(sprite, colour, ti, 7, FoundationPart::Normal);
}
