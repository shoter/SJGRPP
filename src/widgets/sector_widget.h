/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_widget.h Types related to the sector widgets. */

#ifndef WIDGETS_SECTOR_WIDGET_H
#define WIDGETS_SECTOR_WIDGET_H

/** Widgets of the #SectorWindow class. */
enum SectorWidgets : WidgetID {
	WID_SEC_CAPTION,       ///< Caption of the window.
	WID_SEC_MAP,           ///< Map of the sectors.
	WID_SEC_INFO,          ///< Information about the selected sector.
	WID_SEC_BUTTONS_SEL,   ///< Selection of the game or scenario editor buttons.
	WID_SEC_BUY,           ///< Buy building rights.
	WID_SEC_GOTO,          ///< Scroll the main view to the sector.
	WID_SEC_OVERLAY,       ///< Toggle the sector borders in the main view.
	WID_SEC_RENAME,        ///< Rename the sector (scenario editor).
	WID_SEC_PRICE,         ///< Set the price of the sector (scenario editor).
	WID_SEC_FIRST_PICK,    ///< Toggle whether the sector can be the free first sector (scenario editor).
	WID_SEC_PAINT,         ///< Assign tiles to the sector (scenario editor).
	WID_SEC_NEW,           ///< Create a new sector (scenario editor).
	WID_SEC_MERGE,         ///< Merge the sector into another (scenario editor).
	WID_SEC_GENERATE,      ///< Divide the map randomly (scenario editor).
	WID_SEC_EDITOR_GOTO,   ///< Scroll the main view to the sector (scenario editor).
	WID_SEC_EDITOR_OVERLAY,///< Toggle the sector borders in the main view (scenario editor).
	WID_SEC_LASSO,         ///< Assign the inside of a drawn outline to the sector (scenario editor).
	WID_SEC_BRUSH,         ///< Assign the tiles dragged over to the sector (scenario editor).
	WID_SEC_REMOVE_EMPTY,  ///< Remove sectors without tiles (scenario editor).
	WID_SEC_BRUSH_SIZE,    ///< Size of the brush (scenario editor).
	WID_SEC_UNDO,          ///< Undo the last painting (scenario editor).
	WID_SEC_LOCK,          ///< Toggle protecting the tiles of the sector from painting (scenario editor).
};

#endif /* WIDGETS_SECTOR_WIDGET_H */
