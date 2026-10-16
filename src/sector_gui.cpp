/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_gui.cpp GUI for viewing sectors, buying building rights and editing sectors in the scenario editor. */

#include "stdafx.h"
#include "sector_base.h"
#include "sector_cmd.h"
#include "sector_func.h"
#include "blitter/factory.hpp"
#include "command_func.h"
#include "core/backup_type.hpp"
#include "company_base.h"
#include "company_func.h"
#include "core/string_consumer.hpp"
#include "currency_func.h"
#include "dropdown_func.h"
#include "gfx_func.h"
#include "map_func.h"
#include "openttd.h"
#include "palette_func.h"
#include "settings_type.h"
#include "strings_func.h"
#include "textbuf_gui.h"
#include "tilehighlight_func.h"
#include "viewport_func.h"
#include "water_map.h"
#include "window_func.h"
#include "window_gui.h"
#include "zoom_func.h"

#include "widgets/sector_widget.h"

#include "table/sprites.h"
#include "table/strings.h"

#include "safeguards.h"

static constexpr NWidgetPart _nested_sector_widgets[] = {
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_CLOSEBOX, Colours::Brown),
		NWidget(WWT_CAPTION, Colours::Brown, WID_SEC_CAPTION), SetStringTip(STR_SECTOR_CAPTION, STR_TOOLTIP_WINDOW_TITLE_DRAG_THIS),
		NWidget(WWT_SHADEBOX, Colours::Brown),
		NWidget(WWT_DEFSIZEBOX, Colours::Brown),
		NWidget(WWT_STICKYBOX, Colours::Brown),
	EndContainer(),
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_INSET, Colours::Brown, WID_SEC_MAP), SetMinimalSize(320, 180), SetFill(1, 1), SetResize(1, 1), SetToolTip(STR_SECTOR_MAP_TOOLTIP),
		EndContainer(),
		NWidget(WWT_PANEL, Colours::Brown, WID_SEC_INFO), SetMinimalSize(220, 180), SetFill(0, 1), SetResize(0, 1),
		EndContainer(),
	EndContainer(),
	NWidget(NWID_HORIZONTAL),
		NWidget(NWID_SELECTION, Colours::Invalid, WID_SEC_BUTTONS_SEL),
			NWidget(NWID_HORIZONTAL, NWidContainerFlag::EqualSize),
				NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_BUY), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_BUY, STR_SECTOR_BUY_TOOLTIP),
				NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_GOTO), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_GOTO, STR_SECTOR_GOTO_TOOLTIP),
				NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_OVERLAY), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_OVERLAY, STR_SECTOR_OVERLAY_TOOLTIP),
			EndContainer(),
			NWidget(NWID_VERTICAL),
				NWidget(NWID_HORIZONTAL, NWidContainerFlag::EqualSize),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_PAINT), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_PAINT, STR_SECTOR_PAINT_TOOLTIP),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_LASSO), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_LASSO, STR_SECTOR_LASSO_TOOLTIP),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_BRUSH), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_BRUSH, STR_SECTOR_BRUSH_TOOLTIP),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_NEW), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_NEW, STR_SECTOR_NEW_TOOLTIP),
					NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_EDITOR_GOTO), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_GOTO, STR_SECTOR_GOTO_TOOLTIP),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_EDITOR_OVERLAY), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_OVERLAY, STR_SECTOR_OVERLAY_TOOLTIP),
				EndContainer(),
				NWidget(NWID_HORIZONTAL, NWidContainerFlag::EqualSize),
					NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_RENAME), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_RENAME, STR_SECTOR_RENAME_TOOLTIP),
					NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_PRICE), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_PRICE, STR_SECTOR_PRICE_TOOLTIP),
					NWidget(WWT_TEXTBTN, Colours::Brown, WID_SEC_FIRST_PICK), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_FIRST_PICK, STR_SECTOR_FIRST_PICK_TOOLTIP),
					NWidget(WWT_DROPDOWN, Colours::Brown, WID_SEC_MERGE), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_MERGE, STR_SECTOR_MERGE_TOOLTIP),
					NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_REMOVE_EMPTY), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_REMOVE_EMPTY, STR_SECTOR_REMOVE_EMPTY_TOOLTIP),
					NWidget(WWT_PUSHTXTBTN, Colours::Brown, WID_SEC_GENERATE), SetFill(1, 0), SetResize(1, 0), SetStringTip(STR_SECTOR_GENERATE, STR_SECTOR_GENERATE_TOOLTIP),
				EndContainer(),
			EndContainer(),
		EndContainer(),
		NWidget(WWT_RESIZEBOX, Colours::Brown),
	EndContainer(),
};

static WindowDesc _sector_desc(__FILE__, __LINE__,
	WindowPosition::Automatic, "sectors", 0, 0,
	WindowClass::Sectors, WindowClass::None,
	{},
	_nested_sector_widgets
);

/** Colours sectors are painted in on the map, neighbouring sectors get different colours. */
static const Colours _sector_map_colours[] = {
	Colours::PaleGreen, Colours::Pink, Colours::Yellow, Colours::LightBlue, Colours::Cream,
	Colours::Mauve, Colours::Orange, Colours::Grey, Colours::Brown, Colours::Green,
};

/** Window showing a map of the sectors, information about the selected sector, and buttons to buy rights or edit sectors. */
struct SectorWindow : Window {
	SectorID selected = SectorID::Invalid();   ///< The selected sector.
	std::vector<uint8_t> colour_index;         ///< Index into #_sector_map_colours of each sector.
	enum class Query : uint8_t { None, Rename, Price, Generate } query = Query::None; ///< What the open query string is for.
	std::vector<TileIndex> stroke;             ///< Corners of the lasso outline, or brush tiles not yet sent, while dragging.
	TileIndex last_stroke_tile = INVALID_TILE; ///< Last tile below the cursor while dragging with the lasso or brush.

	SectorWindow(WindowDesc &desc, WindowNumber window_number) : Window(desc)
	{
		this->CreateNestedTree();
		this->GetWidget<NWidgetStacked>(WID_SEC_BUTTONS_SEL)->SetDisplayedPlane(_game_mode == GameMode::Editor ? 1 : 0);
		this->FinishInitNested(window_number);

		/* Start with the sector at the centre of the main view. */
		const Window *main = GetMainWindow();
		if (main != nullptr && main->viewport != nullptr) {
			const Viewport &vp = *main->viewport;
			Point pt = TranslateXYToTileCoord(&vp, vp.left + vp.width / 2, vp.top + vp.height / 2);
			if (pt.x >= 0 && pt.y >= 0) this->selected = GetTileSector(TileVirtXY(pt.x, pt.y));
		}
		this->OnInvalidateData(0, true);
	}

	void Close(int data = 0) override
	{
		_highlighted_sector = SectorID::Invalid();
		MarkSectorOverlayDirty();
		this->ClearLasso();
		this->Window::Close();
	}

	/** Colour neighbouring sectors differently. */
	void ComputeColours()
	{
		const uint n = SectorID::End().base();
		this->colour_index.assign(n, 0);
		if (!MapHasSectors()) return;

		std::vector<bool> adjacent(n * n, false);
		const uint size_x = Map::SizeX();
		const uint size_y = Map::SizeY();
		for (uint y = 0; y < size_y; y++) {
			for (uint x = 0; x < size_x; x++) {
				uint8_t a = _sector_map[y * size_x + x];
				if (x + 1 < size_x) {
					uint8_t b = _sector_map[y * size_x + x + 1];
					if (a != b) adjacent[a * n + b] = adjacent[b * n + a] = true;
				}
				if (y + 1 < size_y) {
					uint8_t b = _sector_map[(y + 1) * size_x + x];
					if (a != b) adjacent[a * n + b] = adjacent[b * n + a] = true;
				}
			}
		}

		for (const Sector *s : Sector::Iterate()) {
			const uint i = s->index.base();
			uint32_t used = 0;
			for (const Sector *o : Sector::Iterate()) {
				if (o->index.base() < i && adjacent[i * n + o->index.base()]) SetBit(used, this->colour_index[o->index.base()]);
			}
			uint8_t c = 0;
			while (c < std::size(_sector_map_colours) - 1 && HasBit(used, c)) c++;
			this->colour_index[i] = c;
		}
	}

	/** Geometry of the map widget: tile to pixel projection. */
	struct Projection {
		int width;
		int height;
		float scale; ///< Pixels per tile, horizontally.
		float ox;
		float oy;

		Projection(int width, int height) : width(width), height(height)
		{
			const float span = static_cast<float>(Map::SizeX() + Map::SizeY());
			this->scale = std::min(width / span, 2.0f * height / span);
			this->ox = (width - span * this->scale) / 2.0f;
			this->oy = (height - span * this->scale / 2.0f) / 2.0f;
		}

		/** Get the tile at a pixel of the widget, or INVALID_TILE. */
		TileIndex PixelToTile(int px, int py) const
		{
			const float u = (px + 0.5f - this->ox) / this->scale - Map::SizeX();
			const float s = (py + 0.5f - this->oy) * 2.0f / this->scale;
			const float fx = (s - u) / 2.0f;
			const float fy = (s + u) / 2.0f;
			if (fx < 0 || fy < 0) return INVALID_TILE;
			const uint x = static_cast<uint>(fx);
			const uint y = static_cast<uint>(fy);
			if (x >= Map::SizeX() || y >= Map::SizeY()) return INVALID_TILE;
			return TileXY(x, y);
		}

		/** Get the pixel of the widget showing a tile. */
		Point TileToPixel(TileIndex tile) const
		{
			const float x = TileX(tile) + 0.5f;
			const float y = TileY(tile) + 0.5f;
			return { static_cast<int>(this->ox + (y - x + Map::SizeX()) * this->scale), static_cast<int>(this->oy + (x + y) * this->scale / 2.0f) };
		}
	};

	/** Get the colour a sector is painted in on the map. */
	PixelColour GetSectorColour(SectorID sector, bool water) const
	{
		const Sector *s = Sector::GetIfValid(sector);
		if (s == nullptr) return water ? PC_DARK_BLUE : PC_DARK_GREY;

		Colours colour = _sector_map_colours[this->colour_index[sector.base()]];
		if (_game_mode != GameMode::Editor && Company::IsValidID(_local_company) && s->HasRights(_local_company)) {
			colour = Company::Get(_local_company)->colour;
		}
		Shade shade = (sector == this->selected) ? Shade::Lightest : Shade::Normal;
		if (water) shade = (sector == this->selected) ? Shade::Normal : Shade::Darker;
		return GetColourGradient(colour, shade);
	}

	void DrawMap(const Rect &r) const
	{
		const Rect ir = r.Shrink(WidgetDimensions::scaled.bevel);
		DrawPixelInfo tmp_dpi;
		if (!FillDrawPixelInfo(&tmp_dpi, ir)) return;
		AutoRestoreBackup dpi_backup(_cur_dpi, &tmp_dpi);

		GfxFillRect(0, 0, ir.Width() - 1, ir.Height() - 1, PC_BLACK);
		if (!MapHasSectors()) return;

		const Projection proj(ir.Width(), ir.Height());
		Blitter *blitter = BlitterFactory::GetCurrentBlitter();

		/* Sectors of the previous row of pixels, to find borders. */
		std::vector<uint8_t> previous_row(tmp_dpi.width, SectorID::Invalid().base());
		std::vector<uint8_t> row(tmp_dpi.width);

		for (int j = 0; j < tmp_dpi.height; j++) {
			void *dst = blitter->MoveTo(tmp_dpi.dst_ptr, 0, j);
			uint8_t left = SectorID::Invalid().base();
			for (int i = 0; i < tmp_dpi.width; i++) {
				TileIndex tile = proj.PixelToTile(tmp_dpi.left + i, tmp_dpi.top + j);
				if (tile == INVALID_TILE) {
					row[i] = SectorID::Invalid().base();
					left = row[i];
					continue;
				}
				SectorID sector = GetTileSector(tile);
				row[i] = sector.base();

				const bool border = (i > 0 && left != row[i] && left != SectorID::Invalid().base()) || (j > 0 && previous_row[i] != row[i] && previous_row[i] != SectorID::Invalid().base());
				PixelColour colour;
				if (border) {
					colour = PC_BLACK;
				} else {
					colour = this->GetSectorColour(sector, IsTileType(tile, TileType::Water));
					/* Hatch sectors which cannot be the free first sector in the scenario editor. */
					const Sector *s = Sector::GetIfValid(sector);
					if (_game_mode == GameMode::Editor && s != nullptr && s->no_first_pick && ((tmp_dpi.left + i + tmp_dpi.top + j) % 6) == 0) colour = PC_DARK_RED;
					/* Hatch the sectors the local company owns building rights in. */
					if (_game_mode != GameMode::Editor && s != nullptr && Company::IsValidID(_local_company) && s->HasRights(_local_company) &&
							((tmp_dpi.left + i - tmp_dpi.top - j) & 3) == 0) {
						colour = PC_WHITE;
					}
				}
				blitter->SetPixel(dst, i, 0, colour);
				left = row[i];
			}
			std::swap(row, previous_row);
		}

		/* Sector names at their centres. */
		for (const Sector *s : Sector::Iterate()) {
			if (s->centre == INVALID_TILE) continue;
			Point pt = proj.TileToPixel(s->centre);
			std::string name = GetSectorName(s->index);
			Dimension d = GetStringBoundingBox(name, FontSize::Small);
			if (static_cast<int>(d.width) > ir.Width() / 2) continue;
			DrawString(pt.x - d.width / 2, pt.x + d.width / 2 + 1, pt.y - d.height / 2, name, s->index == this->selected ? TextColour::White : TextColour::Black, AlignmentH::Centre, false, FontSize::Small);
		}
	}

	void DrawInfo(const Rect &r) const
	{
		Rect ir = r.Shrink(WidgetDimensions::scaled.framerect);
		const int line = GetCharacterHeight(FontSize::Normal);

		auto draw = [&](std::string &&text) {
			ir.top = DrawStringMultiLine(ir, text);
		};

		if (_game_mode != GameMode::Editor && !AreSectorsEnabled()) {
			draw(GetString(STR_SECTOR_INFO_DISABLED));
			ir.top += line / 2;
		}

		if (_game_mode != GameMode::Editor && Company::IsValidID(_local_company)) {
			uint owned = 0;
			for (const Sector *s : Sector::Iterate()) {
				if (s->HasRights(_local_company)) owned++;
			}
			draw(GetString(STR_SECTOR_INFO_OWNED_COUNT, owned, Sector::GetNumItems()));
			ir.top += line / 2;
		}

		const Sector *s = Sector::GetIfValid(this->selected);
		if (s == nullptr) {
			draw(GetString(STR_SECTOR_INFO_NONE_SELECTED));
			return;
		}

		draw(GetString(STR_SECTOR_INFO_NAME, GetSectorName(s->index)));
		draw(GetString(STR_SECTOR_INFO_SIZE, s->tile_count));

		if (_game_mode == GameMode::Editor) {
			draw(GetString(s->custom_price > 0 ? STR_SECTOR_INFO_PRICE_CUSTOM : STR_SECTOR_INFO_PRICE_AUTOMATIC, s->GetPrice()));
			draw(GetString(s->no_first_pick ? STR_SECTOR_INFO_FIRST_PICK_FORBIDDEN : STR_SECTOR_INFO_FIRST_PICK_ALLOWED));
			return;
		}

		if (Company::IsValidID(_local_company)) {
			if (s->HasRights(_local_company)) {
				draw(GetString(STR_SECTOR_INFO_HAVE_RIGHTS));
			} else {
				Money price = s->GetPriceFor(_local_company);
				draw(price == 0 ? GetString(STR_SECTOR_INFO_PRICE_FREE) : GetString(STR_SECTOR_INFO_PRICE, price));
				draw(GetString(STR_SECTOR_INFO_NO_RIGHTS));
			}
		} else {
			draw(GetString(STR_SECTOR_INFO_PRICE, s->GetPrice()));
		}
		draw(GetString(s->no_first_pick ? STR_SECTOR_INFO_FIRST_PICK_FORBIDDEN : STR_SECTOR_INFO_FIRST_PICK_ALLOWED));

		ir.top += line / 2;
		draw(GetString(STR_SECTOR_INFO_RIGHTS_HEADER));
		bool any = false;
		for (const Company *c : Company::Iterate()) {
			if (!s->HasRights(c->index)) continue;
			any = true;
			draw(GetString(STR_SECTOR_INFO_RIGHTS_COMPANY, c->index));
			if (ir.top + line > ir.bottom) break;
		}
		if (!any) draw(GetString(STR_SECTOR_INFO_RIGHTS_NONE));
	}

	void DrawWidget(const Rect &r, WidgetID widget) const override
	{
		switch (widget) {
			case WID_SEC_MAP:
				this->DrawMap(r);
				break;

			case WID_SEC_INFO:
				this->DrawInfo(r);
				break;
		}
	}

	void UpdateWidgetSize(WidgetID widget, Dimension &size, [[maybe_unused]] const Dimension &padding, [[maybe_unused]] Dimension &fill, [[maybe_unused]] Dimension &resize) override
	{
		switch (widget) {
			case WID_SEC_MAP:
				size.width = std::max<uint>(size.width, ScaleGUITrad(400));
				size.height = std::max<uint>(size.height, ScaleGUITrad(220));
				break;

			case WID_SEC_INFO:
				size.width = std::max<uint>(size.width, ScaleGUITrad(240));
				break;
		}
	}

	void OnPaint() override
	{
		const Sector *s = Sector::GetIfValid(this->selected);
		const bool can_buy = _game_mode == GameMode::Normal && AreSectorsEnabled() && Company::IsValidID(_local_company) && s != nullptr && !s->HasRights(_local_company);
		this->SetWidgetDisabledState(WID_SEC_BUY, !can_buy);
		this->SetWidgetDisabledState(WID_SEC_GOTO, s == nullptr || s->centre == INVALID_TILE);
		this->SetWidgetDisabledState(WID_SEC_EDITOR_GOTO, s == nullptr || s->centre == INVALID_TILE);
		for (WidgetID w : { WID_SEC_RENAME, WID_SEC_PRICE, WID_SEC_FIRST_PICK, WID_SEC_PAINT, WID_SEC_LASSO, WID_SEC_BRUSH }) {
			this->SetWidgetDisabledState(w, s == nullptr);
		}
		this->SetWidgetDisabledState(WID_SEC_MERGE, s == nullptr || Sector::GetNumItems() < 2);
		this->SetWidgetLoweredState(WID_SEC_FIRST_PICK, s != nullptr && !s->no_first_pick);
		this->SetWidgetLoweredState(WID_SEC_OVERLAY, _show_sector_overlay);
		this->SetWidgetLoweredState(WID_SEC_EDITOR_OVERLAY, _show_sector_overlay);
		this->DrawWidgets();
	}

	void Select(SectorID sector)
	{
		this->selected = sector;
		_highlighted_sector = sector;
		MarkSectorOverlayDirty();
		this->SetDirty();
	}

	void OnClick(Point pt, WidgetID widget, int click_count) override
	{
		switch (widget) {
			case WID_SEC_MAP: {
				const NWidgetBase *nwid = this->GetWidget<NWidgetBase>(WID_SEC_MAP);
				const Rect ir = nwid->GetCurrentRect().Shrink(WidgetDimensions::scaled.bevel);
				if (!MapHasSectors()) break;
				const Projection proj(ir.Width(), ir.Height());
				TileIndex tile = proj.PixelToTile(pt.x - ir.left, pt.y - ir.top);
				if (tile == INVALID_TILE) break;
				this->Select(GetTileSector(tile));
				if (click_count > 1) ScrollMainWindowToTile(tile);
				break;
			}

			case WID_SEC_BUY:
				if (Sector::IsValidID(this->selected)) {
					Command<Commands::BuySectorRights>::Post(STR_ERROR_CAN_T_BUY_SECTOR_RIGHTS, this->selected);
				}
				break;

			case WID_SEC_GOTO:
			case WID_SEC_EDITOR_GOTO: {
				const Sector *s = Sector::GetIfValid(this->selected);
				if (s != nullptr && s->centre != INVALID_TILE) ScrollMainWindowToTile(s->centre);
				break;
			}

			case WID_SEC_OVERLAY:
			case WID_SEC_EDITOR_OVERLAY:
				_show_sector_overlay = !_show_sector_overlay;
				_highlighted_sector = this->selected;
				MarkWholeScreenDirty();
				break;

			case WID_SEC_RENAME: {
				const Sector *s = Sector::GetIfValid(this->selected);
				if (s == nullptr) break;
				this->query = Query::Rename;
				ShowQueryString(s->name, STR_SECTOR_RENAME_QUERY, MAX_LENGTH_SECTOR_NAME_CHARS, this, CS_ALPHANUMERAL, {QueryStringFlag::EnableDefault, QueryStringFlag::LengthIsInChars});
				break;
			}

			case WID_SEC_PRICE: {
				const Sector *s = Sector::GetIfValid(this->selected);
				if (s == nullptr) break;
				this->query = Query::Price;
				ShowQueryString(GetString(STR_JUST_INT, static_cast<int64_t>(s->custom_price * GetCurrency().rate)), STR_SECTOR_PRICE_QUERY, 20, this, CS_NUMERAL, {});
				break;
			}

			case WID_SEC_FIRST_PICK: {
				const Sector *s = Sector::GetIfValid(this->selected);
				if (s == nullptr) break;
				Command<Commands::SectorSetProperties>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, s->index, s->custom_price, !s->no_first_pick);
				break;
			}

			case WID_SEC_PAINT:
			case WID_SEC_LASSO:
			case WID_SEC_BRUSH:
				if (Sector::IsValidID(this->selected)) HandlePlacePushButton(this, widget, SPR_CURSOR_BUY_LAND, widget == WID_SEC_PAINT ? HT_RECT | HT_DIAGONAL : HT_RECT);
				break;

			case WID_SEC_REMOVE_EMPTY:
				Command<Commands::SectorRemoveEmpty>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR);
				break;

			case WID_SEC_NEW:
				HandlePlacePushButton(this, WID_SEC_NEW, SPR_CURSOR_BUY_LAND, HT_RECT);
				break;

			case WID_SEC_MERGE: {
				DropDownList list;
				for (const Sector *s : Sector::Iterate()) {
					if (s->index == this->selected) continue;
					list.push_back(MakeDropDownListStringItem(GetSectorName(s->index), s->index.base()));
				}
				if (!list.empty()) ShowDropDownList(this, std::move(list), -1, widget);
				break;
			}

			case WID_SEC_GENERATE:
				this->query = Query::Generate;
				ShowQueryString(GetString(STR_JUST_INT, _settings_game.economy.sector_size), STR_SECTOR_GENERATE_QUERY, 8, this, CS_NUMERAL, {});
				break;
		}
	}

	void OnDropdownSelect(WidgetID widget, int index, int) override
	{
		if (widget != WID_SEC_MERGE || !Sector::IsValidID(this->selected)) return;
		Command<Commands::SectorMerge>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, this->selected, SectorID(index));
		this->Select(SectorID(index));
	}

	void OnQueryTextFinished(std::optional<std::string> str) override
	{
		Query query = this->query;
		this->query = Query::None;
		if (!str.has_value()) return;

		switch (query) {
			case Query::None:
				break;

			case Query::Rename:
				if (Sector::IsValidID(this->selected)) Command<Commands::SectorRename>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, this->selected, *str);
				break;

			case Query::Price: {
				const Sector *s = Sector::GetIfValid(this->selected);
				if (s == nullptr) break;
				Money price = 0;
				if (!str->empty()) {
					auto value = ParseInteger<uint64_t>(*str, 10, true);
					if (!value.has_value()) break;
					price = *value / GetCurrency().rate;
				}
				Command<Commands::SectorSetProperties>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, s->index, price, s->no_first_pick);
				break;
			}

			case Query::Generate: {
				auto value = ParseInteger<uint32_t>(*str, 10, true);
				if (!value.has_value()) break;
				Command<Commands::SectorGenerate>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, *value);
				break;
			}
		}
	}

	void OnPlaceObject([[maybe_unused]] Point pt, TileIndex tile) override
	{
		if (this->IsWidgetLowered(WID_SEC_PAINT)) {
			VpStartPlaceSizing(tile, VPM_X_AND_Y, DDSP_SECTOR_PAINT);
		} else if (this->IsWidgetLowered(WID_SEC_LASSO) || this->IsWidgetLowered(WID_SEC_BRUSH)) {
			const bool lasso = this->IsWidgetLowered(WID_SEC_LASSO);
			VpStartPlaceSizing(tile, VPM_X_AND_Y, lasso ? DDSP_SECTOR_LASSO : DDSP_SECTOR_BRUSH);
			this->stroke.clear();
			this->last_stroke_tile = INVALID_TILE;
			if (lasso) _sector_lasso_mask.assign(Map::Size(), false);
			this->ExtendStroke(tile, lasso);
			if (!lasso) this->SendBrush();
		} else if (this->IsWidgetLowered(WID_SEC_NEW)) {
			Command<Commands::SectorCreate>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, CommandCallback::SectorCreate, tile);
		}
	}

	/**
	 * Add the tiles between the previous tile of the stroke and a tile to the stroke.
	 * @param tile The tile now below the cursor.
	 * @param lasso Whether the stroke is a lasso outline (only corners are kept) or a brush stroke (every tile is kept).
	 */
	void ExtendStroke(TileIndex tile, bool lasso)
	{
		if (tile == this->last_stroke_tile) return;
		const TileIndex from = this->last_stroke_tile == INVALID_TILE ? tile : this->last_stroke_tile;
		this->last_stroke_tile = tile;
		if (lasso && this->stroke.size() < MAX_SECTOR_PAINT_TILES) this->stroke.push_back(tile);

		/* Fill the gap when the cursor moved more than one tile. */
		const int dx = TileX(tile) - TileX(from);
		const int dy = TileY(tile) - TileY(from);
		const int steps = std::max(std::abs(dx), std::abs(dy));
		for (int k = (steps == 0 ? 0 : 1); k <= steps; k++) {
			const int x = TileX(from) + (steps == 0 ? 0 : (dx * k + (dx > 0 ? steps / 2 : -steps / 2)) / steps);
			const int y = TileY(from) + (steps == 0 ? 0 : (dy * k + (dy > 0 ? steps / 2 : -steps / 2)) / steps);
			const TileIndex t = TileXY(x, y);
			if (lasso) {
				_sector_lasso_mask[t.base()] = true;
				MarkTileDirtyByTile(t);
			} else if (GetTileSector(t) != this->selected && this->stroke.size() < MAX_SECTOR_PAINT_TILES) {
				this->stroke.push_back(t);
			}
		}
	}

	/** Assign the brush tiles collected so far to the selected sector. */
	void SendBrush()
	{
		if (this->stroke.empty() || !Sector::IsValidID(this->selected)) return;
		SectorPaintTilesCmdData data;
		data.sector = this->selected;
		data.polygon = false;
		data.tiles = std::move(this->stroke);
		this->stroke.clear();
		DoCommandP<Commands::SectorPaintTiles>(data, STR_ERROR_CAN_T_CHANGE_SECTOR);
	}

	/** Remove the lasso outline from the main view. */
	void ClearLasso()
	{
		if (_sector_lasso_mask.empty()) return;
		_sector_lasso_mask.clear();
		MarkWholeScreenDirty();
	}

	void OnPlaceDrag(ViewportPlaceMethod select_method, ViewportDragDropSelectionProcess select_proc, Point pt) override
	{
		if (select_proc != DDSP_SECTOR_LASSO && select_proc != DDSP_SECTOR_BRUSH) {
			VpSelectTilesWithMethod(pt.x, pt.y, select_method);
			return;
		}
		if (pt.x < 0 || pt.y < 0) return;
		const uint x = pt.x / TILE_SIZE;
		const uint y = pt.y / TILE_SIZE;
		if (x >= Map::SizeX() || y >= Map::SizeY()) return;

		/* Only highlight the tile below the cursor. */
		_thd.selstart = pt;
		VpSelectTilesWithMethod(pt.x, pt.y, VPM_X_AND_Y);

		const bool lasso = select_proc == DDSP_SECTOR_LASSO;
		this->ExtendStroke(TileXY(x, y), lasso);
		if (!lasso) this->SendBrush();
	}

	void OnPlaceMouseUp([[maybe_unused]] ViewportPlaceMethod select_method, ViewportDragDropSelectionProcess select_proc, Point pt, TileIndex start_tile, TileIndex end_tile) override
	{
		if (select_proc == DDSP_SECTOR_BRUSH) {
			this->SendBrush();
			return;
		}
		if (select_proc == DDSP_SECTOR_LASSO) {
			if (this->stroke.size() >= 3 && Sector::IsValidID(this->selected)) {
				SectorPaintTilesCmdData data;
				data.sector = this->selected;
				data.polygon = true;
				data.tiles = std::move(this->stroke);
				DoCommandP<Commands::SectorPaintTiles>(data, STR_ERROR_CAN_T_CHANGE_SECTOR);
			}
			this->stroke.clear();
			this->ClearLasso();
			return;
		}
		if (pt.x == -1 || select_proc != DDSP_SECTOR_PAINT) return;
		if (!Sector::IsValidID(this->selected)) return;
		Command<Commands::SectorPaint>::Post(STR_ERROR_CAN_T_CHANGE_SECTOR, end_tile, start_tile, this->selected, _ctrl_pressed);
	}

	void OnPlaceObjectAbort() override
	{
		this->RaiseWidgetsWhenLowered(WID_SEC_PAINT, WID_SEC_LASSO, WID_SEC_BRUSH, WID_SEC_NEW);
		this->stroke.clear();
		this->ClearLasso();
	}

	void OnInvalidateData([[maybe_unused]] int data = 0, [[maybe_unused]] bool gui_scope = true) override
	{
		if (!gui_scope) return;
		if (!Sector::IsValidID(this->selected)) this->selected = SectorID::Invalid();
		_highlighted_sector = this->selected;
		this->ComputeColours();
		this->SetDirty();
	}

	void OnHundredthTick() override
	{
		/* Prices change with inflation and money. */
		this->SetWidgetDirty(WID_SEC_INFO);
	}
};

/** Open the sector window. */
void ShowSectorWindow()
{
	AllocateWindowDescFront<SectorWindow>(_sector_desc, 0);
}

/**
 * Callback after creating a sector in the scenario editor: select it, so it can be painted.
 * @param result The result of the command.
 */
void CcSectorCreate(const CommandCost &result)
{
	if (result.Failed()) return;
	auto sector = result.GetResultData<SectorID>();
	if (!sector.has_value()) return;

	SectorWindow *w = dynamic_cast<SectorWindow *>(FindWindowById(WindowClass::Sectors, 0));
	if (w != nullptr) w->Select(*sector);
}

/** Toggle drawing the sector borders in the viewports. */
void ToggleSectorOverlay()
{
	_show_sector_overlay = !_show_sector_overlay;
	MarkWholeScreenDirty();
	SetWindowDirty(WindowClass::Sectors, 0);
}
