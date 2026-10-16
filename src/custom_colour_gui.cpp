/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour_gui.cpp GUI for picking a custom (RGB) company colour. */

#include "stdafx.h"
#include "custom_colour_gui.h"
#include "custom_colour.h"
#include "window_gui.h"
#include "window_func.h"
#include "querystring_gui.h"
#include "slider_func.h"
#include "strings_func.h"
#include "gfx_func.h"
#include "core/string_consumer.hpp"
#include "core/format.hpp"
#include "zoom_func.h"

#include "widgets/custom_colour_widget.h"

#include "table/strings.h"

#include "safeguards.h"

/** Window to pick a custom colour with red, green and blue sliders. */
struct CustomColourPickerWindow : Window {
	std::array<uint8_t, 3> rgb{}; ///< Current red, green and blue components.
	QueryString hex_editbox; ///< Hexadecimal entry of the colour.
	CustomColourPickerCallback *callback; ///< Called with the chosen colour.
	WidgetID parent_widget; ///< Widget of the parent window the colour is for, passed to the callback.
	std::optional<bool> starting_colour; ///< State of the starting colour toggle, if it is offered.

	CustomColourPickerWindow(WindowDesc &desc, Window *parent, WidgetID parent_widget, CustomColour initial, CustomColourPickerCallback *callback, std::optional<bool> starting_colour) :
			Window(desc), hex_editbox(7 * MAX_CHAR_LENGTH, 7), callback(callback), parent_widget(parent_widget), starting_colour(starting_colour)
	{
		this->parent = parent;
		this->SetColour(IsCustomColour(initial) ? initial : MakeCustomColour(128, 128, 128));

		this->querystrings[WID_CCP_HEX] = &this->hex_editbox;
		this->hex_editbox.ok_button = WID_CCP_OK;
		this->hex_editbox.cancel_button = WID_CCP_CANCEL;
		this->hex_editbox.text.afilter = CS_HEXADECIMAL;

		this->CreateNestedTree();
		this->GetWidget<NWidgetStacked>(WID_CCP_STARTING_SEL)->SetDisplayedPlane(this->starting_colour.has_value() ? 0 : SZSP_NONE);
		this->FinishInitNested(0);
		this->SetWidgetLoweredState(WID_CCP_STARTING, this->starting_colour.value_or(false));
		this->UpdateHexText();
	}

	/**
	 * Get the currently chosen colour.
	 * @return The colour.
	 */
	CustomColour GetColour() const
	{
		return MakeCustomColour(this->rgb[0], this->rgb[1], this->rgb[2]);
	}

	/**
	 * Set the currently chosen colour.
	 * @param colour The colour.
	 */
	void SetColour(CustomColour colour)
	{
		this->rgb = { GetCustomColourRed(colour), GetCustomColourGreen(colour), GetCustomColourBlue(colour) };
	}

	/** Update the hexadecimal entry from the chosen colour. */
	void UpdateHexText()
	{
		this->hex_editbox.text.Assign(fmt::format("{:02X}{:02X}{:02X}", this->rgb[0], this->rgb[1], this->rgb[2]));
		this->SetWidgetDirty(WID_CCP_HEX);
	}

	void UpdateWidgetSize(WidgetID widget, Dimension &size, [[maybe_unused]] const Dimension &padding, [[maybe_unused]] Dimension &fill, [[maybe_unused]] Dimension &resize) override
	{
		switch (widget) {
			case WID_CCP_RED_VALUE:
			case WID_CCP_GREEN_VALUE:
			case WID_CCP_BLUE_VALUE:
				size.width = std::max(size.width, GetStringBoundingBox(GetString(STR_CUSTOM_COLOUR_VALUE, 255)).width);
				break;

			case WID_CCP_PREVIEW:
				size.height = std::max<uint>(size.height, ScaleGUITrad(40));
				break;

			case WID_CCP_PRESETS:
				size.height = std::max<uint>(size.height, ScaleGUITrad(12));
				break;
		}
	}

	std::string GetWidgetString(WidgetID widget, StringID stringid) const override
	{
		switch (widget) {
			case WID_CCP_RED_VALUE: return GetString(STR_CUSTOM_COLOUR_VALUE, this->rgb[0]);
			case WID_CCP_GREEN_VALUE: return GetString(STR_CUSTOM_COLOUR_VALUE, this->rgb[1]);
			case WID_CCP_BLUE_VALUE: return GetString(STR_CUSTOM_COLOUR_VALUE, this->rgb[2]);
			default: return this->Window::GetWidgetString(widget, stringid);
		}
	}

	void DrawWidget(const Rect &r, WidgetID widget) const override
	{
		switch (widget) {
			case WID_CCP_PREVIEW: {
				/* The main colour on the left, all eight shades on the right. */
				const CustomColour colour = this->GetColour();
				const Rect ir = r.Shrink(WidgetDimensions::scaled.bevel);
				GfxFillRect(r, PC_BLACK);
				const int split = ir.left + ir.Width() * 2 / 5;
				GfxFillRect(ir.left, ir.top, split - 1, ir.bottom, GetCustomColourGradient(colour, Shade::Light));
				const int shades = to_underlying(Shade::End);
				const int shades_left = split + WidgetDimensions::scaled.bevel.left;
				const int shades_width = ir.right + 1 - shades_left;
				for (int i = 0; i < shades; i++) {
					const int left = shades_left + shades_width * i / shades;
					const int right = shades_left + shades_width * (i + 1) / shades - 1;
					GfxFillRect(left, ir.top, right, ir.bottom, GetCustomColourGradient(colour, static_cast<Shade>(i)));
				}
				break;
			}

			case WID_CCP_RED:
			case WID_CCP_GREEN:
			case WID_CCP_BLUE:
				DrawSliderWidget(r, Colours::Grey, Colours::Grey, TextColour::Black, 0, UINT8_MAX, 0, this->rgb[this->GetComponent(widget)], nullptr);
				break;

			case WID_CCP_PRESETS: {
				const int count = to_underlying(Colours::End);
				for (int i = 0; i < count; i++) {
					const int left = r.left + r.Width() * i / count;
					const int right = r.left + r.Width() * (i + 1) / count - 1;
					GfxFillRect(left, r.top, right, r.bottom, PC_BLACK);
					GfxFillRect(left + 1, r.top + 1, right - 1, r.bottom - 1, GetColourGradient(static_cast<Colours>(i), Shade::Light));
				}
				break;
			}
		}
	}

	/**
	 * Get the colour component index of a slider widget.
	 * @param widget The slider widget.
	 * @return The component: 0 = red, 1 = green, 2 = blue.
	 */
	static uint GetComponent(WidgetID widget)
	{
		switch (widget) {
			case WID_CCP_RED: return 0;
			case WID_CCP_GREEN: return 1;
			default: return 2;
		}
	}

	/** The chosen colour changed, redraw everything which depends on it. */
	void OnColourChanged()
	{
		for (WidgetID widget : { WID_CCP_PREVIEW, WID_CCP_RED, WID_CCP_RED_VALUE, WID_CCP_GREEN, WID_CCP_GREEN_VALUE, WID_CCP_BLUE, WID_CCP_BLUE_VALUE }) {
			this->SetWidgetDirty(widget);
		}
	}

	void OnClick([[maybe_unused]] Point pt, WidgetID widget, [[maybe_unused]] int click_count) override
	{
		switch (widget) {
			case WID_CCP_RED:
			case WID_CCP_GREEN:
			case WID_CCP_BLUE: {
				uint8_t &value = this->rgb[GetComponent(widget)];
				if (ClickSliderWidget(this->GetWidget<NWidgetBase>(widget)->GetCurrentRect(), pt, 0, UINT8_MAX, 0, value)) {
					this->UpdateHexText();
					this->OnColourChanged();
				}
				if (click_count > 0) this->mouse_capture_widget = widget;
				break;
			}

			case WID_CCP_PRESETS: {
				const Rect r = this->GetWidget<NWidgetBase>(widget)->GetCurrentRect();
				const int index = (pt.x - r.left) * to_underlying(Colours::End) / std::max(1, r.Width());
				if (index < 0 || index >= to_underlying(Colours::End)) break;
				this->SetColour(GetPresetColourRGB(static_cast<Colours>(index)));
				this->UpdateHexText();
				this->OnColourChanged();
				break;
			}

			case WID_CCP_STARTING:
				this->starting_colour = !this->starting_colour.value_or(false);
				this->SetWidgetLoweredState(WID_CCP_STARTING, *this->starting_colour);
				this->SetWidgetDirty(WID_CCP_STARTING);
				break;

			case WID_CCP_OK:
				this->callback(this->parent, this->parent_widget, this->GetColour(), this->starting_colour.value_or(false));
				this->Close();
				break;

			case WID_CCP_CANCEL:
				this->Close();
				break;
		}
	}

	void OnEditboxChanged(WidgetID widget) override
	{
		if (widget != WID_CCP_HEX) return;

		std::string_view text = this->hex_editbox.text.GetText();
		if (text.size() != 6) return;
		StringConsumer consumer(text);
		auto value = consumer.TryReadIntegerBase<uint32_t>(16);
		if (!value.has_value() || consumer.AnyBytesLeft()) return;

		this->SetColour(MakeCustomColour(GB(*value, 16, 8), GB(*value, 8, 8), GB(*value, 0, 8)));
		this->OnColourChanged();
	}
};

static constexpr std::initializer_list<NWidgetPart> _nested_custom_colour_picker_widgets = {
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_CLOSEBOX, Colours::Grey),
		NWidget(WWT_CAPTION, Colours::Grey, WID_CCP_CAPTION), SetStringTip(STR_CUSTOM_COLOUR_CAPTION, STR_TOOLTIP_WINDOW_TITLE_DRAG_THIS),
	EndContainer(),
	NWidget(WWT_PANEL, Colours::Grey),
		NWidget(NWID_VERTICAL), SetPadding(WidgetDimensions::unscaled.frametext), SetPIP(0, WidgetDimensions::unscaled.vsep_normal, 0),
			NWidget(WWT_EMPTY, Colours::Invalid, WID_CCP_PREVIEW), SetMinimalSize(240, 0), SetFill(1, 0), SetToolTip(STR_CUSTOM_COLOUR_PREVIEW_TOOLTIP),
			NWidget(NWID_HORIZONTAL), SetPIP(0, WidgetDimensions::unscaled.hsep_wide, 0),
				NWidget(NWID_VERTICAL, NWidContainerFlag::EqualSize), SetPIP(0, WidgetDimensions::unscaled.vsep_normal, 0),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_RED_LABEL), SetStringTip(STR_CUSTOM_COLOUR_RED), SetFill(1, 1),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_GREEN_LABEL), SetStringTip(STR_CUSTOM_COLOUR_GREEN), SetFill(1, 1),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_BLUE_LABEL), SetStringTip(STR_CUSTOM_COLOUR_BLUE), SetFill(1, 1),
				EndContainer(),
				NWidget(NWID_VERTICAL, NWidContainerFlag::EqualSize), SetPIP(0, WidgetDimensions::unscaled.vsep_normal, 0),
					NWidget(WWT_EMPTY, Colours::Invalid, WID_CCP_RED), SetMinimalTextLines(1, 12, FontSize::Small), SetMinimalSize(160, 0), SetFill(1, 1), SetToolTip(STR_CUSTOM_COLOUR_SLIDER_TOOLTIP),
					NWidget(WWT_EMPTY, Colours::Invalid, WID_CCP_GREEN), SetMinimalTextLines(1, 12, FontSize::Small), SetMinimalSize(160, 0), SetFill(1, 1), SetToolTip(STR_CUSTOM_COLOUR_SLIDER_TOOLTIP),
					NWidget(WWT_EMPTY, Colours::Invalid, WID_CCP_BLUE), SetMinimalTextLines(1, 12, FontSize::Small), SetMinimalSize(160, 0), SetFill(1, 1), SetToolTip(STR_CUSTOM_COLOUR_SLIDER_TOOLTIP),
				EndContainer(),
				NWidget(NWID_VERTICAL, NWidContainerFlag::EqualSize), SetPIP(0, WidgetDimensions::unscaled.vsep_normal, 0),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_RED_VALUE), SetAlignment({AlignmentH::ForceRight, AlignmentV::Middle}), SetFill(1, 1),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_GREEN_VALUE), SetAlignment({AlignmentH::ForceRight, AlignmentV::Middle}), SetFill(1, 1),
					NWidget(WWT_TEXT, Colours::Invalid, WID_CCP_BLUE_VALUE), SetAlignment({AlignmentH::ForceRight, AlignmentV::Middle}), SetFill(1, 1),
				EndContainer(),
			EndContainer(),
			NWidget(WWT_EDITBOX, Colours::Grey, WID_CCP_HEX), SetStringTip(STR_CUSTOM_COLOUR_HEX_OSKTITLE, STR_CUSTOM_COLOUR_HEX_TOOLTIP), SetFill(1, 0),
			NWidget(WWT_EMPTY, Colours::Invalid, WID_CCP_PRESETS), SetMinimalSize(240, 0), SetFill(1, 0), SetToolTip(STR_CUSTOM_COLOUR_PRESETS_TOOLTIP),
			NWidget(NWID_SELECTION, Colours::Invalid, WID_CCP_STARTING_SEL),
				NWidget(WWT_TEXTBTN, Colours::Grey, WID_CCP_STARTING), SetFill(1, 0), SetStringTip(STR_CUSTOM_COLOUR_STARTING, STR_CUSTOM_COLOUR_STARTING_TOOLTIP),
			EndContainer(),
		EndContainer(),
	EndContainer(),
	NWidget(NWID_HORIZONTAL, NWidContainerFlag::EqualSize),
		NWidget(WWT_PUSHTXTBTN, Colours::Grey, WID_CCP_OK), SetFill(1, 0), SetStringTip(STR_BUTTON_OK),
		NWidget(WWT_PUSHTXTBTN, Colours::Grey, WID_CCP_CANCEL), SetFill(1, 0), SetStringTip(STR_BUTTON_CANCEL),
	EndContainer(),
};

static WindowDesc _custom_colour_picker_desc(__FILE__, __LINE__,
	WindowPosition::Center, nullptr, 0, 0,
	WindowClass::CustomColourPicker, WindowClass::None,
	WindowDefaultFlag::Construction,
	_nested_custom_colour_picker_widgets
);

/**
 * Show the custom colour picker.
 * @param parent Window the colour is picked for; the picker is closed when this window closes.
 * @param parent_widget Widget of the parent window the colour is for, passed back to the callback.
 * @param initial Initial colour, if it isn't a custom colour a neutral grey is used.
 * @param callback Function to call with the chosen colour when the user confirms it.
 * @param starting_colour If set, offer a toggle to also use the colour as starting colour of new companies, with this initial state.
 */
void ShowCustomColourPicker(Window *parent, WidgetID parent_widget, CustomColour initial, CustomColourPickerCallback *callback, std::optional<bool> starting_colour)
{
	CloseWindowByClass(WindowClass::CustomColourPicker);
	new CustomColourPickerWindow(_custom_colour_picker_desc, parent, parent_widget, initial, callback, starting_colour);
}
