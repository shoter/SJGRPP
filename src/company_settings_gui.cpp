/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file company_settings_gui.cpp Window with the settings of a company, such as the fees it charges for the use of its infrastructure. */

#include "stdafx.h"
#include "command_func.h"
#include "company_base.h"
#include "company_cmd.h"
#include "company_func.h"
#include "company_gui.h"
#include "currency_func.h"
#include "infrastructure_func.h"
#include "settings_gui.h"
#include "strings_func.h"
#include "textbuf_gui.h"
#include "window_func.h"
#include "window_gui.h"
#include "zoom_func.h"
#include "core/geometry_func.hpp"
#include "core/string_consumer.hpp"

#include "widgets/company_widget.h"

#include "table/strings.h"

#include "safeguards.h"

static constexpr NWidgetPart _nested_company_settings_widgets[] = {
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_CLOSEBOX, Colours::Grey),
		NWidget(WWT_CAPTION, Colours::Grey, WID_CSET_CAPTION), SetStringTip(STR_COMPANY_SETTINGS_CAPTION, STR_TOOLTIP_WINDOW_TITLE_DRAG_THIS),
		NWidget(WWT_SHADEBOX, Colours::Grey),
		NWidget(WWT_STICKYBOX, Colours::Grey),
	EndContainer(),
	NWidget(WWT_PANEL, Colours::Grey, WID_CSET_SHARING), SetFill(1, 1), SetToolTip(STR_COMPANY_SETTINGS_SHARING_TOOLTIP),
	EndContainer(),
};

static WindowDesc _company_settings_desc(__FILE__, __LINE__,
	WindowPosition::Automatic, "company_settings", 0, 0,
	WindowClass::CompanySettings, WindowClass::None,
	{},
	_nested_company_settings_widgets
);

/** Label of the sharing fee of each vehicle type. */
static const StringID _sharing_fee_label_strings[] = {
	STR_COMPANY_SETTINGS_SHARING_RAIL,
	STR_COMPANY_SETTINGS_SHARING_ROAD,
	STR_COMPANY_SETTINGS_SHARING_WATER,
	STR_COMPANY_SETTINGS_SHARING_AIR,
};

/** Window with the settings of a company. Only the company itself can change them, others see them read-only. */
struct CompanySettingsWindow : Window {
	static constexpr VehicleType FEE_TYPES[] = { VehicleType::Train, VehicleType::Road, VehicleType::Ship, VehicleType::Aircraft };

	VehicleType query_type = VehicleType::Invalid; ///< Vehicle type of the fee whose amount is being entered.
	int label_width = 0;                            ///< Width of the column with the fee labels.

	CompanySettingsWindow(WindowDesc &desc, WindowNumber window_number) : Window(desc)
	{
		this->InitNested(window_number);
		this->owner = (Owner)this->window_number;
	}

	const Company *GetCompany() const
	{
		return Company::Get((CompanyID)this->window_number);
	}

	std::string GetWidgetString(WidgetID widget, StringID stringid) const override
	{
		if (widget == WID_CSET_CAPTION) return GetString(STR_COMPANY_SETTINGS_CAPTION, (CompanyID)this->window_number);
		return this->Window::GetWidgetString(widget, stringid);
	}

	/** Can the local player change the settings shown in this window? */
	bool IsEditable() const
	{
		return this->window_number == _local_company;
	}

	/**
	 * Get whether the company will charge its own fee once a pending change is in effect.
	 * @param fee The company's fee setting.
	 * @return True if the fee will be the company's own fee.
	 */
	static bool IsTargetCustom(const CompanySharingFee &fee)
	{
		return fee.pending ? fee.pending_custom : fee.custom;
	}

	/**
	 * Get the own fee the company will charge once a pending change is in effect.
	 * @param fee The company's fee setting.
	 * @return The fee, as whole money.
	 */
	static Money GetTargetFee(const CompanySharingFee &fee)
	{
		return fee.pending ? fee.pending_fee : fee.fee;
	}

	int GetRowHeight() const
	{
		return std::max<int>(GetCharacterHeight(FontSize::Normal), SETTING_BUTTON_HEIGHT);
	}

	/** Get the text of the fee in effect for a vehicle type. */
	std::string GetFeeText(VehicleType type) const
	{
		const CompanySharingFee &fee = this->GetCompany()->sharing_fees[to_underlying(type)];
		const Money amount = GetCompanySharingFeeFract(type, (CompanyID)this->window_number) >> 8;
		std::string text = GetString(fee.custom ? STR_COMPANY_SETTINGS_SHARING_OWN_FEE : STR_COMPANY_SETTINGS_SHARING_SERVER_FEE, amount, GetSharingFeeUnitString(type));
		if (!IsInfrastructureSharingEnabled(type)) {
			text += " ";
			text += GetString(STR_COMPANY_SETTINGS_SHARING_DISABLED);
		}
		return text;
	}

	/** Get the text of the announced change of the fee of a vehicle type, or an empty string if there is none. */
	std::string GetPendingText(VehicleType type) const
	{
		const CompanySharingFee &fee = this->GetCompany()->sharing_fees[to_underlying(type)];
		if (!fee.pending) return {};
		const int days = std::max<int>(0, (fee.pending_date - EconTime::CurDate()).base());
		if (!fee.pending_custom) return GetString(STR_COMPANY_SETTINGS_SHARING_PENDING_SERVER, days);
		return GetString(STR_COMPANY_SETTINGS_SHARING_PENDING_OWN, std::min<Money>(fee.pending_fee << 8, GetSharingFeeMaxFract(type)) >> 8, days);
	}

	/** Get the lines of text below the fees. */
	std::vector<std::string> GetNotes() const
	{
		std::vector<std::string> notes;
		if (this->IsEditable()) notes.push_back(GetString(STR_COMPANY_SETTINGS_SHARING_NOTE));
		if (_settings_game.economy.sharing_fee_notice_days > 0) notes.push_back(GetString(STR_COMPANY_SETTINGS_SHARING_NOTE_NOTICE, _settings_game.economy.sharing_fee_notice_days));
		return notes;
	}

	void UpdateWidgetSize(WidgetID widget, Dimension &size, [[maybe_unused]] const Dimension &padding, [[maybe_unused]] Dimension &fill, [[maybe_unused]] Dimension &resize) override
	{
		if (widget != WID_CSET_SHARING) return;

		this->label_width = 0;
		for (VehicleType type : FEE_TYPES) {
			this->label_width = std::max<int>(this->label_width, GetStringBoundingBox(_sharing_fee_label_strings[to_underlying(type)]).width);
		}

		const int row_height = this->GetRowHeight();
		Dimension d = GetStringBoundingBox(STR_COMPANY_SETTINGS_SHARING_TITLE);
		d.height += WidgetDimensions::scaled.vsep_wide;
		uint fee_width = 0;
		for (VehicleType type : FEE_TYPES) {
			fee_width = std::max(fee_width, GetStringBoundingBox(this->GetFeeText(type)).width);
			std::string pending = this->GetPendingText(type);
			if (!pending.empty()) {
				fee_width = std::max(fee_width, GetStringBoundingBox(pending).width);
				d.height += GetCharacterHeight(FontSize::Normal);
			}
			d.height += row_height + WidgetDimensions::scaled.vsep_normal;
		}
		/* Room for larger amounts after inflation. */
		fee_width += ScaleGUITrad(40);
		d.width = std::max<uint>(d.width, SETTING_BUTTON_WIDTH + WidgetDimensions::scaled.hsep_wide + this->label_width + WidgetDimensions::scaled.hsep_wide + fee_width);

		for (const std::string &note : this->GetNotes()) {
			d.height += WidgetDimensions::scaled.vsep_normal + GetStringHeight(note, d.width);
		}

		d.width += WidgetDimensions::scaled.framerect.Horizontal();
		d.height += WidgetDimensions::scaled.framerect.Vertical();
		size = maxdim(size, d);
	}

	/**
	 * Get the top of the row of each fee in the panel.
	 * @param r The rectangle of the panel, without the frame.
	 * @return The top of each row, and the bottom of the last one.
	 */
	std::array<int, std::size(FEE_TYPES) + 1> GetRowTops(const Rect &r) const
	{
		std::array<int, std::size(FEE_TYPES) + 1> tops{};
		int y = r.top + GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_wide;
		for (size_t i = 0; i < std::size(FEE_TYPES); i++) {
			tops[i] = y;
			y += this->GetRowHeight() + WidgetDimensions::scaled.vsep_normal;
			if (!this->GetPendingText(FEE_TYPES[i]).empty()) y += GetCharacterHeight(FontSize::Normal);
		}
		tops[std::size(FEE_TYPES)] = y;
		return tops;
	}

	void DrawWidget(const Rect &r, WidgetID widget) const override
	{
		if (widget != WID_CSET_SHARING) return;

		const Rect ir = r.Shrink(WidgetDimensions::scaled.framerect);
		const bool rtl = _current_text_dir == TD_RTL;
		const Company *c = this->GetCompany();
		const int row_height = this->GetRowHeight();
		const int text_offset = (row_height - GetCharacterHeight(FontSize::Normal)) / 2;
		const int button_offset = (row_height - SETTING_BUTTON_HEIGHT) / 2;

		DrawString(ir.left, ir.right, ir.top, STR_COMPANY_SETTINGS_SHARING_TITLE);

		const auto tops = this->GetRowTops(ir);
		for (size_t i = 0; i < std::size(FEE_TYPES); i++) {
			const VehicleType type = FEE_TYPES[i];
			const CompanySharingFee &fee = c->sharing_fees[to_underlying(type)];
			const Rect row = ir.WithY(tops[i], tops[i] + row_height - 1);

			const Rect button = row.WithWidth(SETTING_BUTTON_WIDTH, rtl);
			DrawBoolButton(button.left, row.top + button_offset, Colours::Yellow, Colours::Grey, IsTargetCustom(fee), this->IsEditable());

			const Rect label = row.Indent(SETTING_BUTTON_WIDTH + WidgetDimensions::scaled.hsep_wide, rtl).WithWidth(this->label_width, rtl);
			DrawString(label.left, label.right, row.top + text_offset, _sharing_fee_label_strings[to_underlying(type)]);

			const Rect value = row.Indent(SETTING_BUTTON_WIDTH + WidgetDimensions::scaled.hsep_wide + this->label_width + WidgetDimensions::scaled.hsep_wide, rtl);
			DrawString(value.left, value.right, row.top + text_offset, this->GetFeeText(type));

			std::string pending = this->GetPendingText(type);
			if (!pending.empty()) DrawString(value.left, value.right, row.bottom + 1, pending);
		}

		int y = tops[std::size(FEE_TYPES)];
		for (const std::string &note : this->GetNotes()) {
			y += WidgetDimensions::scaled.vsep_normal;
			y = DrawStringMultiLine(ir.left, ir.right, y, ir.bottom, note);
		}
	}

	void OnClick(Point pt, WidgetID widget, [[maybe_unused]] int click_count) override
	{
		if (widget != WID_CSET_SHARING || !this->IsEditable()) return;

		const Rect ir = this->GetWidget<NWidgetBase>(widget)->GetCurrentRect().Shrink(WidgetDimensions::scaled.framerect);
		const bool rtl = _current_text_dir == TD_RTL;
		const auto tops = this->GetRowTops(ir);
		for (size_t i = 0; i < std::size(FEE_TYPES); i++) {
			if (pt.y < tops[i] || pt.y >= tops[i] + this->GetRowHeight()) continue;

			const VehicleType type = FEE_TYPES[i];
			const CompanySharingFee &fee = this->GetCompany()->sharing_fees[to_underlying(type)];
			const Rect button = ir.WithWidth(SETTING_BUTTON_WIDTH, rtl);
			const Rect value = ir.Indent(SETTING_BUTTON_WIDTH + WidgetDimensions::scaled.hsep_wide + this->label_width + WidgetDimensions::scaled.hsep_wide, rtl);

			if (pt.x >= button.left && pt.x <= button.right) {
				if (IsTargetCustom(fee)) {
					Command<Commands::SetCompanySharingFee>::Post(STR_ERROR_CAN_T_CHANGE_SHARING_FEE, type, false, 0);
				} else {
					/* Start from the fee in effect, so ticking alone does not change what others pay. */
					const Money start = std::min(GetSharingFeeFract(type), GetSharingFeeMaxFract(type)) >> 8;
					Command<Commands::SetCompanySharingFee>::Post(STR_ERROR_CAN_T_CHANGE_SHARING_FEE, type, true, start);
				}
			} else if (pt.x >= value.left && pt.x <= value.right && IsTargetCustom(fee)) {
				this->query_type = type;
				ShowQueryString(GetString(STR_JUST_INT, GetTargetFee(fee) * GetCurrency().rate),
						GetEncodedString(STR_COMPANY_SETTINGS_SHARING_QUERY_CAPTION, GetSharingFeeMaxFract(type) >> 8), 15, this, CS_NUMERAL, {});
			}
			return;
		}
	}

	void OnQueryTextFinished(std::optional<std::string> str) override
	{
		const VehicleType type = this->query_type;
		this->query_type = VehicleType::Invalid;
		if (!str.has_value() || type == VehicleType::Invalid) return;

		auto value = ParseInteger<uint64_t>(*str, 10, true);
		if (!value.has_value()) return;
		const Money fee = static_cast<Money>(*value / GetCurrency().rate);
		Command<Commands::SetCompanySharingFee>::Post(STR_ERROR_CAN_T_CHANGE_SHARING_FEE, type, true, fee);
	}

	void OnInvalidateData([[maybe_unused]] int data = 0, [[maybe_unused]] bool gui_scope = true) override
	{
		if (!gui_scope) return;
		this->ReInit();
	}

	void OnHundredthTick() override
	{
		/* Server fees change with inflation and settings, and the notice periods count down. */
		this->ReInit();
	}
};

/**
 * Open the settings window of a company.
 * @param company The company.
 */
void ShowCompanySettings(CompanyID company)
{
	if (!Company::IsValidID(company)) return;
	AllocateWindowDescFront<CompanySettingsWindow>(_company_settings_desc, company);
}
